
# Email Sender

Bulk plain-text email sender over Gmail SMTP (STARTTLS, port 587), driven by JSON
config files. It rotates through a list of sender accounts, keeps a resume state
file so an interrupted run can continue where it stopped, and retries transient
SMTP failures with backoff.

Stdlib-only at runtime — no external packages are needed to send.

## Requirements

- Python 3.8+
- One or more Gmail accounts using
  [App Passwords](https://myaccount.google.com/apppasswords). Normal Gmail
  passwords are rejected; Google blocks this unless 2-Step Verification is
  enabled and an App Password is generated.

## Setup

```bash
./run.sh              # create .venv, install deps, then build email_body.json
./run.sh --recreate   # wipe and recreate the venv first
./run.sh --no-setup   # venv + deps only
./run.sh --run --dry-run   # bootstrap, then preview what would be sent
```

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
python setup.py            # interactive: writes email_body.json
python main.py --dry-run   # preview
python main.py             # send
```

## Configuration files

All files are read from the current working directory.

| File | Tracked? | Format |
| --- | --- | --- |
| `senders.json` | gitignored | `[{"email": "...", "password": "..."}]` |
| `recievers.json` | gitignored | `["a@b.com", "c@d.com"]` |
| `email_body.json` | gitignored | `{"Subject": "...", "Body": "..."}` |
| `*.json` `_example` variants | committed | copy them and fill in real values |

Recipients may also be named `receivers.json`; both spellings are accepted and
`recievers.json` (the historical filename) wins only if `receivers.json` is
absent. Addresses are trimmed, lowercased, validated, and de-duplicated on load.

Runtime artifacts, also gitignored: `mailer.log` (full run log),
`.mailer_state.json` (resume progress), `__pycache__/`.

> **Note:** `.env` is not read by `main.py`. Sender credentials come from
> `senders.json` only. `python-dotenv` in `requirements.txt` is currently unused.

## Usage

```
python main.py [--dry-run] [--delay SECONDS] [--no-resume] [-v]
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Report what would be sent, including how many recipients are already covered by the resume state. Sends nothing and writes no state. |
| `--delay SECONDS` | Base pause between sends (default `2.0`). Actual sleep is jittered to 50–150% of the value so the cadence looks less scripted. |
| `--no-resume` | Ignore `.mailer_state.json` and mail every recipient again. |
| `-v`, `--verbose` | `DEBUG` logging, including per-recipient resume skips. |

### Behaviour worth knowing

- **Fan-out:** every sender mails every recipient. Two accounts and 500
  recipients means 1000 messages, and each account gets its own entry in the
  state file.
- **Resume:** progress is written to `.mailer_state.json` after each successful
  send, so a crash or `Ctrl-C` loses at most one message. Pressing `Ctrl-C`
  returns exit code `130`.
- **Retries:** up to 3 attempts per recipient with 5 s / 10 s backoff. A dropped
  connection triggers a reconnect + login and the same recipient is retried.
  A refused recipient is not retried.
- **Timing:** timeouts are 30 s per socket operation.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | everything sent |
| `1` | configuration error (missing/invalid JSON, no senders, no valid recipients) |
| `2` | run completed but at least one recipient failed |
| `130` | interrupted |

## Security

- `senders.json` holds App Passwords in plaintext. It is gitignored — keep it
  that way, and never commit `.env` or any credential file.
- Gmail enforces sending quotas (roughly 500 recipients/day for consumer
  accounts) and flags high-volume scripted sending as spam. Expect soft bounces
  or account suspension if you push volume too fast; raise `--delay` and split
  runs across days rather than adding accounts.
- If a credential has ever appeared in git history, revoke it at
  https://myaccount.google.com/apppasswords and generate a new one. Removing the
  file in a later commit does not remove it from history.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Login failed for ...` | Not an App Password, 2-Step Verification is off, or "Less secure app access" was removed for that account. |
| `Configuration error: email_body.json: 'Body' is empty.` | Re-run `python setup.py` and terminate the body with a line containing only `END`. |
| Nothing sent but exit `0` | Resume state already covered the recipients. `main.py --dry-run` shows the counts; `--no-resume` forces a full send. |
| `Connection error for ...` | Port 25/587 blocked (common on cloud hosts and some ISPs), or no route to `smtp.gmail.com`. |
| Recipient skipped unexpectedly | It is listed under that sender in `.mailer_state.json`. Delete the file, or the sender's entry in it, to resend. |

## Layout

```
main.py       sender: load config, SMTP session, retries, resume state
setup.py      interactive editor for email_body.json
run.sh        bootstrap (venv + deps + setup.py) and optional send
```

`setup.py` is a project script, not a setuptools build file; rename it (for
example `setup_email.py`) before packaging this project.
