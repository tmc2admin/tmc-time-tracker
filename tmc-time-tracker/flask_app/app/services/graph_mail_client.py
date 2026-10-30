# app/services/graph_mail_client.py
"""Thin wrapper around Microsoft Graph for the Hausverwaltung mail follow-up feature.

Deliberately not using Authlib's request-bound OAuth client here: this needs to run from a
background/webjob context against a stored refresh token, not from an interactive browser
request. See app/helpers/crypto.py for how the refresh token is encrypted at rest, and
app/blueprints/auth.py for where it is first captured during Microsoft login.
"""

import logging
from datetime import datetime

import requests
from flask import current_app

from .. import db
from ..helpers.crypto import encrypt_text, decrypt_text, TokenEncryptionNotConfigured

log = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
REQUIRED_SCOPE = 'Mail.Read Mail.ReadWrite offline_access'


class MailAccessUnavailable(RuntimeError):
    """Raised when a user has no usable mail token (never consented, or refresh failed)."""


def _token_endpoint():
    authority = current_app.config.get('OAUTH_AUTHORITY')
    if not authority:
        raise MailAccessUnavailable('OAUTH_AUTHORITY is not configured.')
    return f"{authority}/oauth2/v2.0/token"


def get_valid_access_token(user):
    """Return a fresh Graph access token for `user`, refreshing + rotating the stored
    refresh token as needed.

    Raises MailAccessUnavailable if the user never granted the mail scopes, or the refresh
    token has been revoked/expired - in both cases they need to log in again and accept the
    Mail.Read/Mail.ReadWrite consent prompt.
    """
    if not user.ms_mail_refresh_token_enc:
        raise MailAccessUnavailable(
            f'{user.email} has not granted mail access yet (no stored refresh token). '
            'They need to log in again and accept the additional permissions prompt.'
        )
    try:
        refresh_token = decrypt_text(user.ms_mail_refresh_token_enc)
    except TokenEncryptionNotConfigured as exc:
        raise MailAccessUnavailable(str(exc)) from exc

    client_id = current_app.config.get('OAUTH_CLIENT_ID')
    client_secret = current_app.config.get('OAUTH_CLIENT_SECRET')
    resp = requests.post(
        _token_endpoint(),
        data={
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'scope': REQUIRED_SCOPE,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        log.warning(
            f"Graph token refresh failed for {user.email}: {resp.status_code} {resp.text[:300]}"
        )
        raise MailAccessUnavailable(
            f'Token refresh failed for {user.email} ({resp.status_code}). '
            'They likely need to log in again.'
        )
    data = resp.json()

    # Microsoft rotates refresh tokens on every use - always persist the newest one,
    # otherwise the next refresh will fail with an "already used" error.
    new_refresh_token = data.get('refresh_token')
    if new_refresh_token:
        user.ms_mail_refresh_token_enc = encrypt_text(new_refresh_token)
        user.ms_mail_token_updated_at = datetime.utcnow()
        db.session.commit()

    return data['access_token']


def _headers(access_token):
    return {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}


def _get_paginated(url, access_token, params=None, max_items=200):
    items = []
    while url and len(items) < max_items:
        resp = requests.get(url, headers=_headers(access_token), params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get('value', []))
        url = data.get('@odata.nextLink')
        params = None  # nextLink already encodes the query string
    return items[:max_items]


def list_recent_messages(access_token, folder, since_iso, top=100):
    """List messages in a well-known mail folder ('inbox' or 'sentitems') received on or
    after `since_iso` (an ISO-8601 UTC timestamp, e.g. '2026-10-20T00:00:00Z'), newest first.
    """
    url = f"{GRAPH_BASE}/me/mailFolders/{folder.lower()}/messages"
    params = {
        '$filter': f"receivedDateTime ge {since_iso}",
        '$select': 'id,conversationId,subject,from,toRecipients,receivedDateTime,webLink,bodyPreview',
        '$orderby': 'receivedDateTime desc',
        '$top': min(top, 100),
    }
    return _get_paginated(url, access_token, params=params, max_items=top)


def create_draft(access_token, to_email, subject, body_text, in_reply_to_message_id=None):
    """Create an Outlook draft.

    If `in_reply_to_message_id` is given, creates it as a reply draft on that message's
    conversation (Graph pre-fills recipients/threading/quoted body), then overwrites the body
    with `body_text`. Otherwise creates a standalone new-message draft addressed to `to_email`.
    Returns the created/updated Graph message resource (includes 'id' and 'webLink').
    """
    if in_reply_to_message_id:
        reply_url = f"{GRAPH_BASE}/me/messages/{in_reply_to_message_id}/createReply"
        resp = requests.post(reply_url, headers=_headers(access_token), json={}, timeout=20)
        resp.raise_for_status()
        draft = resp.json()

        patch_url = f"{GRAPH_BASE}/me/messages/{draft['id']}"
        patch_resp = requests.patch(
            patch_url,
            headers=_headers(access_token),
            json={'body': {'contentType': 'Text', 'content': body_text}},
            timeout=20,
        )
        patch_resp.raise_for_status()
        return patch_resp.json()

    url = f"{GRAPH_BASE}/me/messages"
    resp = requests.post(
        url,
        headers=_headers(access_token),
        json={
            'subject': subject,
            'body': {'contentType': 'Text', 'content': body_text},
            'toRecipients': [{'emailAddress': {'address': to_email}}],
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()
