"""Email (Phase 7b.5).

The rules every message follows, from the gap review's email findings:

- **Emails only notify.** A message says what happened and links to the
  page in the app where it is seen or decided. No link acts on a click:
  mail scanners (Gmail, Safe Links, antivirus) open links by themselves,
  and a one-click "approve" would approve. Decisions stay authenticated
  requests made in the app.
- **No conversation content,** and no free text from the model or from
  employees: only fields the platform knows (an assistant's name, a
  person's name, a version number), with line breaks and web addresses
  stripped from each, so nothing written into a name can forge a header or
  a link.
- **Plain text.**
- **Settings in the environment only** (`SMTP_*`, `MAIL_FROM`), never
  editable in the app.

Sending is a background job on its own queue (`app.worker.MailWorkerSettings`)
with retries, recorded in `mail_messages` (`app.services.mail`).
"""

from app.mail.message import Mail, clean_field

__all__ = ["Mail", "clean_field"]
