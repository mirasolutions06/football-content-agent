# Runbook

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
make install
make test
make db-init
```

`make test` runs the 67-test suite. It does not need live API credentials.

## Environment

Copy `.env.example` to `.env` for local development, or set
`MANDEM_ENV_FILE` to point at a server environment file.

Required for live operation:

- `MANDEM_BOT_TOKEN` for Telegram approval DMs.
- `MJ_MANDEM_CHAT_ID` for the operator approval chat.
- `APISPORTS_KEY` or `RAPIDAPI_KEY` for API-Football.
- `FAL_KEY` for Seedream stylization.
- `GEMINI_API_KEY` for overlay and vision checks.
- `OPENAI_API_KEY` or `MANDEM_OPENAI_KEY` for generated fallback images.

Optional:

- `BRAVE_API_KEY` for better image search.
- `PEXELS_API_KEY` for stock fallback images.
- `MANDEM_DATA_DIR` for the SQLite/images/queue directory.
- `MANDEM_ENV_FILE` for server-side env loading outside git.

## Common Commands

```bash
make test                  # run unit tests
make smoke                 # check env and RSS feeds
make db-init               # create/update SQLite schema
python3 scripts/mandem_mcp.py
python3 scripts/mandem_db.py query "SELECT id, status FROM post_drafts ORDER BY id DESC LIMIT 5"
python3 scripts/smoke.py --live
```

`python3 scripts/smoke.py --live` calls API-Football and should be used
deliberately on free-tier keys.

## Server Wiring

Register `scripts/mandem_mcp.py` as a stdio MCP server in your agent runtime.
Pass only the env vars that Mandem needs. Keep the env file outside git and make
sure the process user can read it.

Suggested runtime settings:

- Start command: `python3`.
- Args: `scripts/mandem_mcp.py`.
- Working directory: repo root.
- Data directory: set `MANDEM_DATA_DIR` to a persistent server directory.
- Secrets: set `MANDEM_ENV_FILE` to a protected server environment file.

## Operational Loop

1. Poll fixture/news tools.
2. Save a draft with an image path and caption.
3. Send the Telegram approval DM.
4. On approval, submit an async stylization job.
5. Poll `check_stylize` until done or failed.
6. Deliver the final preview.
7. Publish or manually upload, depending on account setup.

## Before Making Public Changes

```bash
make test
```

Run a sensitive-string scan for live hostnames, IPs, chat IDs, secrets, and
private paths before publishing.
