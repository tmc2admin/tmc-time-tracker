# Hausverwaltungen - Mail Follow-Up Feature

Admin dashboard section (`/admin/mail_followups`) that tracks open email threads with
Hausverwaltungen (property managers) around the Telekom Glasfaser rollout, flags ones
that still need a reply ("Wiedervorlage"), and drafts German reply text in Torsten's
style via the Claude API for review before it's placed as an Outlook draft.

## How it fits together

- `app/models.py`: `MailWatchedDomain` (which senders/keywords count as "Hausverwaltung"),
  `MailFollowUp` (one row per email conversation), and three new columns on `User` for the
  encrypted Graph refresh token.
- `app/services/graph_mail_client.py`: talks to Microsoft Graph directly (token refresh,
  list messages, create drafts) - independent of Authlib's request-bound OAuth client so it
  can run from a webjob/background context.
- `app/services/draft_service.py`: calls the Anthropic Claude API to (re)generate reply text.
- `app/services/mail_followup_service.py`: orchestrates the above - the actual "check
  mailbox, diff against Sent Items, assign due dates, fill in drafts" logic.
- `app/blueprints/admin.py`: `/admin/mail_followups` (UI), `/admin/api/mail_followups/*`
  (list/edit/regenerate/create-draft), and `/admin/api/run_mail_followup_check` (the daily
  entry point, same webjob Basic-Auth pattern as the existing
  `/admin/api/enforce_auto_clock_out`).
- `app/templates/admin/mail_followups.html` + `app/static/js/mail_followups.js`: the UI.

## One-time setup (required before this does anything)

1. **Azure AD app registration - API permissions.** The app already has an Azure AD
   registration for Microsoft SSO login (`OAUTH_CLIENT_ID`/`OAUTH_AUTHORITY` - see
   `app/__init__.py`). Add delegated permissions **Mail.Read** and **Mail.ReadWrite** to that
   same registration (Azure Portal -> App registrations -> this app -> API permissions ->
   Add a permission -> Microsoft Graph -> Delegated permissions), and grant admin consent.
   `offline_access` is included by default for delegated permissions, no separate entry needed.
2. **Re-login.** Existing users (in practice: Torsten) need to log out and back in via
   "Login mit Microsoft" once, so they see and accept the new consent prompt. The refresh
   token is only captured at that point (`app/blueprints/auth.py`,
   `_store_mail_refresh_token_if_present`).
3. **Environment variables** (set alongside the existing `OAUTH_CLIENT_ID` /
   `OAUTH_CLIENT_SECRET` / `OAUTH_AUTHORITY` / `WEBJOB_USERNAME` / `WEBJOB_PASSWORD`):
   - `TOKEN_ENCRYPTION_KEY` - a Fernet key used to encrypt the stored Graph refresh token.
     Generate once and never rotate casually (rotating it invalidates every stored token,
     forcing a re-login):
     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
   - `ANTHROPIC_API_KEY` - for draft generation. Without it, the feature still tracks open/
     answered/overdue status, it just can't write draft text (the row's `draft_error` will
     say so, and the UI shows "Mit Claude verfeinern" failing with a clear message).
   - `ANTHROPIC_MODEL` (optional, defaults to `claude-sonnet-5-5`).
4. **Daily trigger.** Point whatever already calls `/admin/api/enforce_auto_clock_out` (an
   Azure WebJob / scheduled task, same Basic Auth with `WEBJOB_USERNAME`/`WEBJOB_PASSWORD`)
   at `POST /admin/api/run_mail_followup_check` too, on a weekday-morning schedule
   (e.g. 07:00 Europe/Berlin). It runs once per user who has a stored mail token - in
   practice just Torsten for now.
5. **Watched domains.** Seeded via migration with the Hausverwaltungen already seen in real
   correspondence (hv-philipp.de, format-hv.de, schoenundsever.de, grk.de) plus a few urgency
   keywords (dringend, wiedervorlage, hausverwaltung). Add/remove more under
   `/admin/mail_watched_domains` as new companies come up - no redeploy needed.

## Design choices worth knowing about

- **Drafts are never sent automatically.** The daily job only fills in `draft_text` on the
  `MailFollowUp` row; writing an actual Outlook draft happens only when a human clicks
  "Als Outlook-Entwurf anlegen" in the UI, after reviewing/editing the text. There is no
  "auto-send" path at all currently.
- **"Beantwortet" detection** is a simple heuristic: if there's a Sent Items message on the
  same `conversationId` with a later timestamp than the latest matching inbound message,
  it's considered answered. A conversation that was marked `done` by a human is never
  auto-reopened even if Graph later shows no reply - only `open`/`answered`/`snoozed`
  conversations get re-evaluated.
- **Lookback window** is 21 days (`LOOKBACK_DAYS` in `mail_followup_service.py`); a first
  Wiedervorlage is set 3 days out (`WIEDERVORLAGE_DAYS`) for newly-opened or reopened threads.
- Only `bodyPreview` (Graph's ~255 char snippet) is used as model input for drafting, not the
  full message body, to keep what leaves the server to Anthropic minimal. If draft quality
  turns out too thin in practice, switching to the full body is a one-line change in
  `graph_mail_client.list_recent_messages` (`$select`) plus passing it through.
