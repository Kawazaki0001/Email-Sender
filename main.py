"""Bulk email sender using multiple Gmail accounts.

Usage:
    python main.py [--dry-run] [--delay SECONDS] [--no-resume] [-v]

Expected files in the working directory:
    senders.json     - list of {"email": "...", "password": "..."}
    receivers.json   - list of email addresses ("recievers.json" also accepted)
    email_body.json  - {"Subject": "...", "Body": "..."}

Run `python setup.py` to build email_body.json interactively.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import smtplib
import ssl
import sys
import time
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
DEFAULT_DELAY = 2.0
MAX_RETRIES = 3
RETRY_BACKOFF = 5.0
LOG_FILE = Path("mailer.log")
STATE_FILE = Path(".mailer_state.json")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

log = logging.getLogger("mailer")


# --------------------------------------------------------------------------- #
# Logging / IO helpers
# --------------------------------------------------------------------------- #
def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Configuration loaders
# --------------------------------------------------------------------------- #
def load_receivers() -> list[str]:
    """Load, validate, dedupe recipient addresses."""
    for name in ("receivers.json", "recievers.json"):
        path = Path(name)
        if path.exists():
            data = load_json(path)
            break
    else:
        raise RuntimeError("receivers.json (or recievers.json) not found")

    if not isinstance(data, list):
        raise ValueError("Receivers file must contain a JSON list.")

    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, str):
            log.warning("Skipping non-string receiver: %r", entry)
            continue
        addr = entry.strip().lower()
        if not EMAIL_RE.match(addr):
            log.warning("Skipping invalid email: %r", entry)
            continue
        if addr in seen:
            log.debug("Skipping duplicate receiver: %s", addr)
            continue
        seen.add(addr)
        cleaned.append(addr)
    return cleaned


def load_email_body() -> tuple[str, str]:
    data = load_json(Path("email_body.json"))
    if not isinstance(data, dict):
        raise ValueError("email_body.json must be a JSON object.")
    subject = str(data.get("Subject", "")).strip()
    body = str(data.get("Body", ""))
    if not subject:
        raise ValueError("email_body.json: 'Subject' is empty.")
    if not body.strip():
        raise ValueError("email_body.json: 'Body' is empty.")
    return subject, body


def load_accounts(filename: str = "senders.json") -> list[dict]:
    data = load_json(Path(filename))
    if not isinstance(data, list):
        raise ValueError(f"{filename} must contain a JSON list of accounts.")

    accounts: list[dict] = []
    for i, acc in enumerate(data, 1):
        if not isinstance(acc, dict):
            raise ValueError(f"Account #{i} must be a JSON object.")
        email = str(acc.get("email", "")).strip()
        password = str(acc.get("password", ""))
        if not email or not password:
            raise ValueError(f"Account #{i} needs 'email' and 'password'.")
        if not EMAIL_RE.match(email):
            raise ValueError(f"Account #{i} has an invalid email: {email}")
        accounts.append({"email": email, "password": password})
    return accounts


# --------------------------------------------------------------------------- #
# Resume state
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"sent": {}}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"sent": {}}
        if not isinstance(data.get("sent"), dict):
            # Older/hand-edited state files may not use the dict shape.
            data["sent"] = {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read state file (%s); starting fresh.", exc)
        return {"sent": {}}


def save_state(state: dict) -> None:
    try:
        with STATE_FILE.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError as exc:
        log.warning("Could not save state: %s", exc)


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def build_message(subject: str, body: str, recipient: str, sender: str) -> MIMEText:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = sender
    msg["To"] = recipient
    return msg


def _reconnect(server: smtplib.SMTP, email: str, password: str) -> None:
    server.connect(SMTP_SERVER, SMTP_PORT)
    server.ehlo()
    server.starttls(context=ssl.create_default_context())
    server.ehlo()
    server.login(email, password)


def send_emails(
    account: dict,
    receivers: list[str],
    subject: str,
    body: str,
    state: dict,
    delay: float,
    dry_run: bool,
    resume: bool,
) -> tuple[int, int]:
    sender_email = account["email"]
    sender_password = account["password"]
    sent_set: list[str] = state["sent"].setdefault(sender_email, [])

    log.info("=== Sender: %s ===", sender_email)

    already_sent = set(sent_set)
    pending = [r for r in receivers if not (resume and r in already_sent)]

    if dry_run:
        skipped = len(receivers) - len(pending)
        log.info("[DRY RUN] Would send %d email(s) via %s (%d already sent)",
                 len(pending), sender_email, skipped)
        return 0, 0

    if not pending:
        log.info("All %d receiver(s) already sent via %s; nothing to do.",
                 len(receivers), sender_email)
        return 0, 0

    sent_count = 0
    failed_count = 0
    context = ssl.create_default_context()

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            try:
                server.login(sender_email, sender_password)
            except smtplib.SMTPAuthenticationError as exc:
                log.error("Login failed for %s: %s", sender_email, exc)
                return 0, len(pending)

            total = len(pending)
            for i, recipient in enumerate(pending, 1):
                msg = build_message(subject, body, recipient, sender_email)
                success = False

                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        server.send_message(
                            msg, from_addr=sender_email, to_addrs=[recipient]
                        )
                        log.info("[%d/%d] Sent -> %s", i, total, recipient)
                        sent_set.append(recipient)
                        sent_count += 1
                        success = True
                        break

                    except smtplib.SMTPRecipientsRefused as exc:
                        log.error("Recipient refused %s: %s",
                                  recipient, exc.recipients)
                        failed_count += 1
                        break

                    except smtplib.SMTPServerDisconnected:
                        log.warning("Server disconnected; reconnecting...")
                        try:
                            _reconnect(server, sender_email, sender_password)
                            continue  # retry same recipient
                        except smtplib.SMTPException as exc2:
                            log.error("Reconnect failed: %s", exc2)
                            failed_count += 1
                            break

                    except (smtplib.SMTPException, OSError) as exc:
                        if attempt < MAX_RETRIES:
                            wait = RETRY_BACKOFF * attempt
                            log.warning(
                                "Attempt %d/%d failed for %s (%s). "
                                "Retrying in %.0fs",
                                attempt, MAX_RETRIES, recipient, exc, wait,
                            )
                            time.sleep(wait)
                        else:
                            log.error("Giving up on %s: %s", recipient, exc)
                            failed_count += 1
                            break

                if success:
                    save_state(state)  # incremental write

                if success and i < total:
                    # Jittered delay looks more human than fixed sleep.
                    time.sleep(max(0.1, delay * random.uniform(0.5, 1.5)))

    except (smtplib.SMTPException, OSError) as exc:
        # connect/ehlo/starttls blew up: everything not yet accounted for failed.
        log.error("Connection error for %s: %s", sender_email, exc)
        failed_count = len(pending) - sent_count

    log.info(
        "Sender %s -> sent=%d failed=%d", sender_email, sent_count, failed_count
    )
    return sent_count, failed_count


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk email sender")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Seconds between sends (default {DEFAULT_DELAY})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without sending")
    parser.add_argument("--no-resume", action="store_true",
                        help="Ignore .mailer_state.json and resend to everyone")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    try:
        accounts = load_accounts()
        receivers = load_receivers()
        subject, body = load_email_body()
    except (RuntimeError, ValueError) as exc:
        log.error("Configuration error: %s", exc)
        return 1

    if not accounts:
        log.error("No sender accounts configured.")
        return 1
    if not receivers:
        log.error("No valid receivers configured.")
        return 1

    state = {"sent": {}} if args.no_resume else load_state()

    log.info("Loaded %d sender(s), %d unique receiver(s).",
             len(accounts), len(receivers))

    total_sent = 0
    total_failed = 0

    try:
        for account in accounts:
            s, f = send_emails(
                account, receivers, subject, body, state,
                delay=args.delay,
                dry_run=args.dry_run,
                resume=not args.no_resume,
            )
            total_sent += s
            total_failed += f
    except KeyboardInterrupt:
        log.warning("Interrupted; saving progress.")
        save_state(state)
        return 130

    save_state(state)
    log.info("DONE. total_sent=%d total_failed=%d", total_sent, total_failed)
    return 0 if total_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
