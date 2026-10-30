# app/services/mail_followup_service.py
"""Orchestrates the Hausverwaltung mail follow-up feature: pulls recent mail via Graph,
figures out which conversations are still open, assigns Wiedervorlage dates, and keeps
MailFollowUp rows (and their draft texts) in sync. See docs/mail_followups.md for the
feature overview and required configuration.
"""

import logging
from datetime import datetime, timedelta, date

from sqlalchemy import case

from .. import db
from ..models import MailFollowUp, MailWatchedDomain
from . import graph_mail_client
from . import draft_service

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 21
# How many days out a freshly-opened (or reopened) item gets its first Wiedervorlage.
WIEDERVORLAGE_DAYS = 3


def _domain_of(email_address):
    if not email_address or '@' not in email_address:
        return None
    return email_address.rsplit('@', 1)[-1].lower()


def _matches_watched(message, watched_domains, watched_keywords):
    """Returns (matched, label). `watched_domains` maps lowercase domain -> label."""
    sender = (message.get('from') or {}).get('emailAddress') or {}
    sender_domain = _domain_of((sender.get('address') or '').lower())
    if sender_domain and sender_domain in watched_domains:
        return True, watched_domains[sender_domain]

    haystack = f"{message.get('subject') or ''} {message.get('bodyPreview') or ''}".lower()
    for keyword in watched_keywords:
        if keyword in haystack:
            return True, None
    return False, None


def _parse_graph_datetime(value):
    if not value:
        return None
    value = value.replace('Z', '')
    fmt = '%Y-%m-%dT%H:%M:%S.%f' if '.' in value else '%Y-%m-%dT%H:%M:%S'
    return datetime.strptime(value, fmt)


class MailFollowUpService:

    @staticmethod
    def sync_user(user):
        """Pull recent Hausverwaltung-relevant mail for `user`, create/update MailFollowUp
        rows, and (re)generate missing draft texts.

        Raises graph_mail_client.MailAccessUnavailable if the user has no usable Graph
        token at all (never consented, or refresh failed) - there is nothing useful to do
        in that case, so callers (the webjob endpoint) should catch it per-user and move on
        rather than letting one broken account abort the whole run. Per-message problems
        (a single draft failing to generate) are recorded on that row instead and never
        raise, so one bad draft can't break the rest of the sync.
        """
        summary = {'checked': 0, 'new_open': 0, 'newly_answered': 0, 'overdue': 0, 'draft_errors': 0}

        access_token = graph_mail_client.get_valid_access_token(user)

        since_iso = (datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
        inbox_messages = graph_mail_client.list_recent_messages(access_token, 'inbox', since_iso)
        sent_messages = graph_mail_client.list_recent_messages(access_token, 'sentitems', since_iso)

        watched_rows = MailWatchedDomain.query.all()
        watched_domains = {r.domain.lower(): r.label for r in watched_rows if not r.is_keyword}
        watched_keywords = [r.domain.lower() for r in watched_rows if r.is_keyword]

        last_outbound_by_conversation = {}
        for msg in sent_messages:
            conv = msg.get('conversationId')
            sent_at = _parse_graph_datetime(msg.get('receivedDateTime'))
            if not conv or not sent_at:
                continue
            if conv not in last_outbound_by_conversation or sent_at > last_outbound_by_conversation[conv]:
                last_outbound_by_conversation[conv] = sent_at

        # Keep only the latest matching inbound message per conversation.
        latest_inbound_by_conversation = {}
        for msg in inbox_messages:
            matched, label = _matches_watched(msg, watched_domains, watched_keywords)
            if not matched:
                continue
            conv = msg.get('conversationId')
            received_at = _parse_graph_datetime(msg.get('receivedDateTime'))
            if not conv or not received_at:
                continue
            current = latest_inbound_by_conversation.get(conv)
            if not current or received_at > current[1]:
                latest_inbound_by_conversation[conv] = (msg, received_at, label)

        today = date.today()

        for conv, (msg, received_at, label) in latest_inbound_by_conversation.items():
            summary['checked'] += 1
            sender = (msg.get('from') or {}).get('emailAddress') or {}
            sender_email = sender.get('address')
            sender_name = sender.get('name')

            last_outbound = last_outbound_by_conversation.get(conv)
            answered = bool(last_outbound and last_outbound > received_at)

            row = MailFollowUp.query.filter_by(user_id=user.id, conversation_id=conv).first()
            is_new = row is None
            was_open = (not is_new) and row.status in (MailFollowUp.STATUS_OPEN, MailFollowUp.STATUS_SNOOZED)
            if is_new:
                row = MailFollowUp(user_id=user.id, conversation_id=conv)
                db.session.add(row)

            row.source_message_id = msg.get('id')
            row.web_link = msg.get('webLink')
            row.subject = msg.get('subject')
            row.counterpart_name = sender_name
            row.counterpart_email = sender_email
            row.company_label = label or _domain_of(sender_email)
            row.last_inbound_at = received_at
            row.last_outbound_at = last_outbound
            row.last_inbound_preview = msg.get('bodyPreview')

            if answered:
                if was_open:
                    summary['newly_answered'] += 1
                row.status = MailFollowUp.STATUS_ANSWERED
                row.due_date = None
                continue

            if row.status == MailFollowUp.STATUS_DONE:
                # Explicitly closed by the user - don't resurrect it just because the
                # thread still has no later outbound message on record.
                continue

            reopening = is_new or row.status == MailFollowUp.STATUS_ANSWERED
            if reopening:
                summary['new_open'] += 1
                row.status = MailFollowUp.STATUS_OPEN
                row.due_date = today + timedelta(days=WIEDERVORLAGE_DAYS)
                row.draft_text = None  # force a fresh draft against the new message
            elif row.due_date and row.due_date <= today:
                summary['overdue'] += 1

            if not row.draft_text:
                try:
                    row.draft_text = draft_service.generate_reply_draft(
                        sender_name=sender_name,
                        sender_company=row.company_label,
                        subject=row.subject,
                        inbound_body=row.last_inbound_preview,
                    )
                    row.draft_generated_at = datetime.utcnow()
                    row.draft_error = None
                except draft_service.DraftGenerationUnavailable as exc:
                    row.draft_error = str(exc)[:500]
                    summary['draft_errors'] += 1

        db.session.commit()
        return summary

    @staticmethod
    def list_followups(user_id, status=None):
        query = MailFollowUp.query.filter_by(user_id=user_id)
        if status:
            query = query.filter_by(status=status)
        # SQL Server has no NULLS LAST, so order null due_dates last explicitly.
        return query.order_by(
            case((MailFollowUp.due_date.is_(None), 1), else_=0),
            MailFollowUp.due_date.asc(),
            MailFollowUp.last_inbound_at.desc(),
        ).all()

    @staticmethod
    def update_draft_text(followup_id, text):
        row = MailFollowUp.query.get(followup_id)
        if not row:
            return None
        row.draft_text = text
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @staticmethod
    def set_status(followup_id, status, due_date=None):
        row = MailFollowUp.query.get(followup_id)
        if not row:
            return None
        row.status = status
        row.due_date = due_date
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @staticmethod
    def regenerate_draft(followup_id, feedback=None):
        row = MailFollowUp.query.get(followup_id)
        if not row:
            return None
        try:
            row.draft_text = draft_service.generate_reply_draft(
                sender_name=row.counterpart_name,
                sender_company=row.company_label,
                subject=row.subject,
                inbound_body=row.last_inbound_preview,
                feedback=feedback,
            )
            row.draft_generated_at = datetime.utcnow()
            row.draft_error = None
        except draft_service.DraftGenerationUnavailable as exc:
            row.draft_error = str(exc)[:500]
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row

    @staticmethod
    def create_outlook_draft(user, followup_id):
        row = MailFollowUp.query.get(followup_id)
        if not row:
            return None
        access_token = graph_mail_client.get_valid_access_token(user)
        result = graph_mail_client.create_draft(
            access_token,
            to_email=row.counterpart_email,
            subject=f"AW: {row.subject}" if row.subject else 'Antwort',
            body_text=row.draft_text or '',
            in_reply_to_message_id=row.source_message_id,
        )
        row.outlook_draft_id = result.get('id')
        row.outlook_draft_web_link = result.get('webLink')
        row.outlook_draft_created_at = datetime.utcnow()
        db.session.commit()
        return row
