"""Outgoing email for account verification codes (FR-AUTH-09).

Configure in .env:

    MORVIX_EMAIL_MODE=smtp            # smtp | console (console = development only)
    MORVIX_SMTP_HOST=smtp.gmail.com
    MORVIX_SMTP_PORT=587              # 587 = STARTTLS, 465 = SSL
    MORVIX_SMTP_USER=your.address@gmail.com
    MORVIX_SMTP_PASSWORD=<Gmail app password, 16 characters>
    MORVIX_SMTP_FROM=MORVIX <your.address@gmail.com>

Gmail requires 2-Step Verification and an App Password (Google Account -> Security ->
App passwords); the normal Gmail password is rejected. Email is only used to send
verification codes; it is never used for content classification (NI-02).
"""
import os, smtplib, ssl, sys, time
from email.message import EmailMessage
from email.utils import formataddr


class MailError(Exception):
    pass


def _cfg(name, default=""):
    return os.getenv("MORVIX_" + name, default).strip()


def mode() -> str:
    m = _cfg("EMAIL_MODE").lower()
    if m in ("smtp", "console"):
        return m
    return "smtp" if _cfg("SMTP_HOST") else ""


def configured() -> bool:
    m = mode()
    if m == "console":
        return True
    return m == "smtp" and bool(_cfg("SMTP_HOST") and _cfg("SMTP_USER") and _cfg("SMTP_PASSWORD"))


def send_mail(to: str, subject: str, text: str, html: str | None = None) -> None:
    m = mode()
    if not configured():
        raise MailError("Email verification is not configured on this server. Set the MORVIX_SMTP_* values in .env (see README).")
    if m == "console":  # development only: the message is printed to the server console, not emailed
        print(f"\n[MORVIX DEV EMAIL - not sent] To: {to}\nSubject: {subject}\n{text}\n", flush=True)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _cfg("SMTP_FROM") or formataddr(("MORVIX", _cfg("SMTP_USER")))
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    host, port = _cfg("SMTP_HOST"), int(_cfg("SMTP_PORT", "587") or 587)
    for attempt in (1, 2):  # one retry: a dropped connection is often a one-off on the network or server side
        step = "connect"
        try:
            ctx = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
                    step = "login"; s.login(_cfg("SMTP_USER"), _cfg("SMTP_PASSWORD"))
                    step = "send"; s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=20) as s:
                    step = "starttls"; s.starttls(context=ctx)
                    step = "login"; s.login(_cfg("SMTP_USER"), _cfg("SMTP_PASSWORD"))
                    step = "send"; s.send_message(msg)
            return
        except smtplib.SMTPAuthenticationError as e:
            _log(step, e, attempt)
            raise MailError("The email server rejected the MORVIX sender login. For Gmail, use an App Password, not the normal password.")
        except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError) as e:
            _log(step, e, attempt)
            if attempt == 1:
                time.sleep(1.5)
                continue
            raise MailError(f"Could not send the verification email: the email server closed the connection during {step} ({type(e).__name__}). "
                            "Try again in a minute; if it keeps happening, see data/server-error.log.")
        except (OSError, smtplib.SMTPException) as e:
            _log(step, e, attempt)
            raise MailError(f"Could not send the verification email ({type(e).__name__} during {step}). Check the SMTP settings and the internet connection.")


def _log(step, e, attempt):
    """Server-side detail for troubleshooting (data/server-error.log). Never includes the password."""
    detail = getattr(e, "smtp_error", b"")
    detail = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail or e)
    print(f"[MORVIX email] attempt {attempt}: {step} failed: {type(e).__name__} {getattr(e, 'smtp_code', '')} {detail}".strip(),
          file=sys.stderr, flush=True)
