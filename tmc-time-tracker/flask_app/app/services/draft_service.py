# app/services/draft_service.py
"""Generates German reply drafts for the Hausverwaltung mail follow-up feature, in
Torsten's own style, via the Anthropic Claude API.

Kept separate from mail_followup_service.py so the LLM call (the one truly optional,
"nice to have but can fail" dependency in this feature) is isolated and easy to mock in
tests or swap out later.
"""

import logging
import os

log = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-sonnet-5-5'

# A few real, lightly-trimmed examples of Torsten's own sent mail, used as a style anchor.
# Keep these short and few - the point is tone (direct, no filler, informal-but-respectful,
# comfortable with minor typos) not exhaustive coverage.
STYLE_EXAMPLES = """\
Beispiel 1 (an eine Hausverwaltung, nach einer Terminabsage):
"Hallo Wolfgang,

Herr Struck hat Erkältungsbeding abgesagt und möchte auf nächste Woche verschieben. Siehe Mail unten! Lass uns nachher telefonieren, wo wir uns treffen.

Gruß Torsten"

Beispiel 2 (an eine externe Stelle, kurz und sachlich):
"Guten Tag,

Der Angang fehlt. Können sie diesen bitte noch mal zukommen lassen?

Mit freundlichen Grüßen

Torsten Müller
Geschäftsführer

TM-Connect
Am Kleingewerbegebiet 16
15745 Wildau"

Beispiel 3 (an einen Kollegen, Aufgabe delegiert):
"Guten Morgen,

Am besten frag noch mal bei CGI welches Angebot sie zurück benötigen und auf was man sich geeinigt hat.

Bg

Mit freundlichen Grüßen

Torsten Müller
Geschäftsführer"
"""

SYSTEM_PROMPT = f"""Du hilfst Torsten Müller (Geschäftsführer, TM-Connect GmbH) dabei, Antworten auf \
geschäftliche E-Mails von Hausverwaltungen zu entwerfen (Telekom-Glasfaser-Ausbau, \
Wohnungswirtschaft).

Schreibe IMMER auf Deutsch, in SEINEM Stil:
- kurz und direkt, keine Floskeln, keine langen Einleitungen
- höflich aber informell-pragmatisch, wie unter Geschäftspartnern die sich kennen
- bei externen Kontakten: "Sie", Signatur mit "Mit freundlichen Grüßen / Torsten Müller / \
Geschäftsführer / TM-Connect / Am Kleingewerbegebiet 16 / 15745 Wildau"
- schreibe fehlerfrei (keine Tippfehler imitieren), aber halte den knappen, direkten Ton

Hier sind echte Beispiele seines Schreibstils zur Orientierung:

{STYLE_EXAMPLES}

Du bekommst den Betreff und Inhalt der eingehenden Mail, die beantwortet werden soll. \
Antworte NUR mit dem fertigen Mailtext (inkl. Anrede und Signatur), ohne Erklärungen, \
ohne Anführungszeichen, ohne Markdown-Formatierung."""


class DraftGenerationUnavailable(RuntimeError):
    """Raised when no draft could be generated (missing API key, API error, etc.)."""


def _client():
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise DraftGenerationUnavailable(
            'ANTHROPIC_API_KEY is not set - draft generation is disabled until it is configured.'
        )
    try:
        import anthropic
    except ImportError as exc:
        raise DraftGenerationUnavailable(
            "The 'anthropic' package is not installed (pip install anthropic)."
        ) from exc
    return anthropic.Anthropic(api_key=api_key)


def generate_reply_draft(sender_name, sender_company, subject, inbound_body, feedback=None):
    """Generate (or regenerate) a reply draft for one inbound message.

    `feedback` is Torsten's own instruction when asking for a revision
    (e.g. "kürzer" or "schlage Dienstag und Mittwoch als Termine vor"); omit for a first draft.
    Returns the draft text (str). Raises DraftGenerationUnavailable on any failure - callers
    should catch this and store the error on the MailFollowUp row rather than crashing the
    whole sync run over one bad draft.
    """
    client = _client()
    model = os.getenv('ANTHROPIC_MODEL', DEFAULT_MODEL)

    user_message = (
        f"Absender: {sender_name or 'unbekannt'}"
        + (f" ({sender_company})" if sender_company else "")
        + f"\nBetreff: {subject or '(ohne Betreff)'}\n\n"
        f"Nachricht:\n{inbound_body or '(kein Inhalt)'}\n"
    )
    if feedback:
        user_message += f"\nTorstens Anmerkung zur Überarbeitung: {feedback}\n"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user_message}],
        )
    except Exception as exc:  # noqa: BLE001 - any SDK/network error should degrade gracefully
        log.warning(f"Claude draft generation failed: {exc}")
        raise DraftGenerationUnavailable(str(exc)) from exc

    text_parts = [block.text for block in response.content if getattr(block, 'type', None) == 'text']
    draft = '\n'.join(text_parts).strip()
    if not draft:
        raise DraftGenerationUnavailable('Claude returned an empty draft.')
    return draft
