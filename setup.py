"""Interactive helper to populate email_body.json.

NOTE: This file is named setup.py only because the project already uses that
name. It clashes with the setuptools `setup.py` convention — if you ever ship
this as a package, rename it to something like `setup_email.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

EMAIL_BODY_FILE = Path("email_body.json")


def load_existing() -> dict:
    if not EMAIL_BODY_FILE.exists():
        return {"Subject": "", "Body": ""}
    try:
        with EMAIL_BODY_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {
                "Subject": str(data.get("Subject", "") or ""),
                "Body": str(data.get("Body", "") or ""),
            }
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read existing {EMAIL_BODY_FILE}: {exc}")
    return {"Subject": "", "Body": ""}


def prompt_subject(existing: str) -> str:
    hint = f" [{existing}]" if existing else ""
    while True:
        subject = input(f"Subject of email{hint}: ").strip()
        if subject:
            return subject
        if existing:
            return existing
        print("  Subject cannot be empty. Try again.")


def prompt_body(existing: str) -> str:
    print()
    print("Body of email.")
    if existing:
        print(f"  Current body has {len(existing)} characters.")
        print("  Press Enter on a blank line to keep the current body,")
        print("  or type new text. Terminate with a line containing only END.")
    else:
        print("  Type your message. Terminate with a line containing only END.")
    print()

    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)

    new_body = "\n".join(lines).strip("\n")
    if new_body.strip():
        return new_body
    if existing:
        return existing
    print("  No body entered.")
    return ""


def save(obj: dict) -> None:
    with EMAIL_BODY_FILE.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main() -> None:
    existing = load_existing()

    print("=== Email Body Setup ===")
    print(f"Target file: {EMAIL_BODY_FILE.resolve()}")
    print()

    subject = prompt_subject(existing["Subject"])
    body = prompt_body(existing["Body"])

    save({"Subject": subject, "Body": body})

    print()
    print(f"Saved {EMAIL_BODY_FILE}.")
    print(f"  Subject: {subject}")
    print(f"  Body:    {len(body)} character(s)")


if __name__ == "__main__":
    main()
