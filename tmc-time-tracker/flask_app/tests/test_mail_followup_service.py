import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ['SECRET_KEY'] = 'mail-followup-service-test'
os.environ['DATABASE_URL'] = 'sqlite://'

from app import create_app, db
from app.models import MailFollowUp, MailWatchedDomain, User
from app.services import draft_service
from app.services.mail_followup_service import MailFollowUpService


def _message(conversation_id, sender_email, sender_name, subject, received_at,
             body_preview='Bitte um kurze Rückmeldung.', message_id=None):
    return {
        'id': message_id or f'msg-{conversation_id}',
        'conversationId': conversation_id,
        'subject': subject,
        'from': {'emailAddress': {'address': sender_email, 'name': sender_name}},
        'receivedDateTime': received_at,
        'webLink': f'https://outlook.office365.com/{conversation_id}',
        'bodyPreview': body_preview,
    }


class MailFollowUpServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.user = User(username='Torsten', email='torsten@tm-connect.de', microsoft_oid='oid-1')
        self.user.ms_mail_refresh_token_enc = 'irrelevant-for-these-tests'
        db.session.add(self.user)
        db.session.add(MailWatchedDomain(domain='hv-philipp.de', label='B. Philipp Hausverwaltungs GmbH'))
        db.session.add(MailWatchedDomain(domain='dringend', is_keyword=True))
        db.session.commit()

        # Every test patches the Graph token lookup so no real network call happens.
        patcher = patch('app.services.mail_followup_service.graph_mail_client.get_valid_access_token',
                         return_value='fake-token')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _sync_with(self, inbox_messages, sent_messages=None, draft_text='Entwurfstext.'):
        with patch('app.services.mail_followup_service.graph_mail_client.list_recent_messages') as mock_list, \
             patch('app.services.mail_followup_service.draft_service.generate_reply_draft',
                   return_value=draft_text):
            def side_effect(access_token, folder, since_iso, top=100):
                return inbox_messages if folder == 'inbox' else (sent_messages or [])
            mock_list.side_effect = side_effect
            return MailFollowUpService.sync_user(self.user)

    def test_new_unanswered_message_from_watched_domain_becomes_open_with_draft(self):
        messages = [_message('conv-1', 'p.struck@hv-philipp.de', 'P. Struck',
                              'Besprechung Ausbau', '2026-10-29T07:58:46.000Z')]

        summary = self._sync_with(messages, draft_text='Guten Tag Herr Struck, ...')

        self.assertEqual(summary['new_open'], 1)
        self.assertEqual(summary['newly_answered'], 0)

        row = MailFollowUp.query.filter_by(conversation_id='conv-1').one()
        self.assertEqual(row.status, MailFollowUp.STATUS_OPEN)
        self.assertEqual(row.company_label, 'B. Philipp Hausverwaltungs GmbH')
        self.assertEqual(row.draft_text, 'Guten Tag Herr Struck, ...')
        self.assertEqual(row.due_date, date.today() + timedelta(days=3))

    def test_message_already_answered_is_not_flagged_open(self):
        inbox = [_message('conv-2', 'p.struck@hv-philipp.de', 'P. Struck',
                           'Termin', '2026-10-29T07:58:46.000Z')]
        sent = [_message('conv-2', 'torsten@tm-connect.de', 'Torsten', 'AW: Termin',
                          '2026-10-29T08:09:38.000Z', message_id='sent-1')]

        summary = self._sync_with(inbox, sent)

        self.assertEqual(summary['new_open'], 0)
        row = MailFollowUp.query.filter_by(conversation_id='conv-2').one()
        self.assertEqual(row.status, MailFollowUp.STATUS_ANSWERED)
        self.assertIsNone(row.due_date)
        self.assertIsNone(row.draft_text)

    def test_keyword_match_surfaces_mail_from_unknown_domain(self):
        messages = [_message('conv-3', 'someone@unknown-hv.example', 'Jemand',
                              'Bitte DRINGEND antworten', '2026-10-29T07:58:46.000Z')]

        self._sync_with(messages)

        row = MailFollowUp.query.filter_by(conversation_id='conv-3').one()
        self.assertEqual(row.status, MailFollowUp.STATUS_OPEN)
        self.assertEqual(row.company_label, 'unknown-hv.example')

    def test_unrelated_sender_is_ignored(self):
        messages = [_message('conv-4', 'newsletter@telekom.de', 'Telekom',
                               'up2date Privatkunden', '2026-10-29T07:58:46.000Z')]

        summary = self._sync_with(messages)

        self.assertEqual(summary['checked'], 0)
        self.assertIsNone(MailFollowUp.query.filter_by(conversation_id='conv-4').first())

    def test_answered_thread_reopens_on_new_inbound_message(self):
        existing = MailFollowUp(
            user_id=self.user.id, conversation_id='conv-5', status=MailFollowUp.STATUS_ANSWERED,
            subject='Alt', draft_text='alter entwurf',
        )
        db.session.add(existing)
        db.session.commit()

        messages = [_message('conv-5', 'p.struck@hv-philipp.de', 'P. Struck',
                              'Neue Frage', '2026-10-30T09:00:00.000Z')]

        summary = self._sync_with(messages, draft_text='neuer entwurf')

        self.assertEqual(summary['new_open'], 1)
        row = MailFollowUp.query.filter_by(conversation_id='conv-5').one()
        self.assertEqual(row.status, MailFollowUp.STATUS_OPEN)
        self.assertEqual(row.draft_text, 'neuer entwurf')
        self.assertEqual(row.due_date, date.today() + timedelta(days=3))

    def test_done_followup_is_never_auto_reopened(self):
        existing = MailFollowUp(
            user_id=self.user.id, conversation_id='conv-6', status=MailFollowUp.STATUS_DONE,
            subject='Geklärt',
        )
        db.session.add(existing)
        db.session.commit()

        messages = [_message('conv-6', 'p.struck@hv-philipp.de', 'P. Struck',
                              'Geklärt', '2026-10-29T07:58:46.000Z')]

        self._sync_with(messages)

        row = MailFollowUp.query.filter_by(conversation_id='conv-6').one()
        self.assertEqual(row.status, MailFollowUp.STATUS_DONE)

    def test_overdue_open_item_is_counted_without_losing_its_draft(self):
        existing = MailFollowUp(
            user_id=self.user.id, conversation_id='conv-7', status=MailFollowUp.STATUS_OPEN,
            subject='Alt', draft_text='bestehender entwurf',
            due_date=date.today() - timedelta(days=1),
        )
        db.session.add(existing)
        db.session.commit()

        messages = [_message('conv-7', 'p.struck@hv-philipp.de', 'P. Struck',
                              'Alt', '2026-10-20T07:58:46.000Z')]

        summary = self._sync_with(messages)

        self.assertEqual(summary['overdue'], 1)
        row = MailFollowUp.query.filter_by(conversation_id='conv-7').one()
        self.assertEqual(row.draft_text, 'bestehender entwurf')  # untouched, not regenerated

    def test_draft_generation_failure_is_recorded_not_raised(self):
        messages = [_message('conv-8', 'p.struck@hv-philipp.de', 'P. Struck',
                              'Termin', '2026-10-29T07:58:46.000Z')]

        with patch('app.services.mail_followup_service.graph_mail_client.list_recent_messages') as mock_list, \
             patch('app.services.mail_followup_service.draft_service.generate_reply_draft',
                   side_effect=draft_service.DraftGenerationUnavailable('ANTHROPIC_API_KEY missing')):
            mock_list.side_effect = lambda access_token, folder, since_iso, top=100: (
                messages if folder == 'inbox' else []
            )
            summary = MailFollowUpService.sync_user(self.user)

        self.assertEqual(summary['draft_errors'], 1)
        row = MailFollowUp.query.filter_by(conversation_id='conv-8').one()
        self.assertIsNone(row.draft_text)
        self.assertIn('ANTHROPIC_API_KEY', row.draft_error)


if __name__ == '__main__':
    unittest.main()
