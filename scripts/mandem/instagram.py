#!/usr/bin/env python3
# scripts/mandem/instagram.py
# Publish to Instagram via Meta's Instagram API with Instagram Login (graph.instagram.com).
# Single-image post, two-call container -> publish flow. Plus long-lived-token refresh.
#
# Account/app config lives in env:
#   MANDEM_IG_USER_ID, MANDEM_IG_APP_ID, MANDEM_IG_APP_SECRET, MANDEM_IG_TOKEN (bootstrap)
# The live token is kept in a private file (MANDEM_IG_TOKEN_FILE) so the
# refresh cron can rotate it without touching the server env file. On first use we
# bootstrap that file from MANDEM_IG_TOKEN (once, only if the file does not yet exist).
#
#   python3 -m scripts.mandem.instagram whoami     # token sanity check (prints id/username)
#   python3 -m scripts.mandem.instagram refresh    # refresh long-lived token (~50d cron)

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

from . import _env

# IG access tokens travel in request query strings here (graph.instagram.com). httpx logs
# full request URLs at INFO, which would leak the token to journald if INFO logging is on.
# Pin httpx/httpcore to WARNING so the token never reaches the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

GRAPH = "https://graph.instagram.com"
API_VERSION = "v21.0"

# Token lives in a dedicated 0700 dir so the secret file is not merely 0600 inside a
# world-traversable data dir.
DEFAULT_TOKEN_FILE = _env.data_dir() / ".ig" / "ig_token"

# Keep the synchronous publish path comfortably under Hermes' 120s MCP RPC timeout.
HTTP_TIMEOUT = 25.0


class PublishUncertain(RuntimeError):
    """Raised when a publish MAY have gone live but we lost the response (timeout / dropped
    connection on media_publish). The post must NOT be auto-retried — a human reconciles."""


# ---------- token handling (file-backed so the refresh cron can rotate it) ----------

def _token_file() -> Path:
    _env.load()
    return Path(os.environ.get("MANDEM_IG_TOKEN_FILE") or DEFAULT_TOKEN_FILE)


def _read_token() -> str:
    """Live token. Prefer the rotatable file. Bootstrap from MANDEM_IG_TOKEN env ONLY when
    the file does not yet exist. An existing-but-empty/corrupt file is an error, not a
    silent re-seed (re-seeding could resurrect a stale/expired bootstrap token)."""
    f = _token_file()
    if f.exists():
        tok = f.read_text().strip()
        if tok:
            return tok
        raise RuntimeError(
            f"IG token file {f} exists but is empty/corrupt — refusing to silently re-seed "
            f"from the (possibly stale) env token. Investigate / re-run `instagram refresh`."
        )
    _env.load()
    env_tok = (os.environ.get("MANDEM_IG_TOKEN") or "").strip()
    if not env_tok:
        raise RuntimeError("No IG token: set MANDEM_IG_TOKEN in env (or the token file).")
    _write_token(env_tok)  # one-time bootstrap of the file
    return env_tok


def _write_token(token: str) -> None:
    """Atomically write the token at 0600 inside a 0700 dir. Never leaves a world-readable
    window (create-with-mode + atomic replace) and never silently leaves loose perms."""
    token = token.strip()
    if not token:
        raise ValueError("refusing to write an empty IG token")
    f = _token_file()
    d = f.parent
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # mkdir mode is umask-masked; pin it explicitly
    except OSError:
        pass  # parent perms are defense-in-depth; the 0600 file below is the real guard
    tmp = f.with_name(f".{f.name}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode())
    finally:
        os.close(fd)
    os.replace(str(tmp), str(f))  # atomic; preserves the 0600 source perms
    # Verify perms are restrictive; if we genuinely can't secure the file, fail loudly.
    mode = os.stat(f).st_mode & 0o777
    if mode & 0o077:
        try:
            os.chmod(f, 0o600)
        except OSError as e:
            raise RuntimeError(f"could not secure token file perms on {f}: {e}") from e


def _user_id() -> str:
    _env.load()
    uid = os.environ.get("MANDEM_IG_USER_ID")
    if not uid:
        raise RuntimeError("MANDEM_IG_USER_ID not set in secrets.env")
    return uid


# ---------- API helpers ----------

def _raise_ig(r: httpx.Response) -> None:
    if r.status_code >= 400:
        msg = None
        try:
            body = r.json()
            if isinstance(body, dict):
                err = body.get("error", {})
                if isinstance(err, dict):
                    msg = f"{err.get('message')} (code {err.get('code')}, subcode {err.get('error_subcode')})"
        except ValueError:
            pass
        if not msg:
            msg = r.text[:300]
        raise RuntimeError(f"IG API {r.status_code}: {msg}")


def whoami() -> dict:
    """Confirm the token works. Returns {id, username, user_id}. NOTE: the value that goes
    in MANDEM_IG_USER_ID for publishing is the `id` field from /me."""
    token = _read_token()
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        r = c.get(f"{GRAPH}/me", params={"fields": "id,user_id,username", "access_token": token})
        _raise_ig(r)
        return r.json()


def _wait_ready(c: httpx.Client, creation_id: str, token: str, attempts: int = 4, delay: float = 1.5) -> None:
    """Poll the media container until FINISHED. Photos are usually instant; this is a guard
    bounded to stay well under the MCP RPC timeout."""
    for _ in range(attempts):
        r = c.get(
            f"{GRAPH}/{API_VERSION}/{creation_id}",
            params={"fields": "status_code", "access_token": token},
        )
        if r.status_code == 200:
            sc = r.json().get("status_code")
            if sc == "FINISHED":
                return
            if sc == "ERROR":
                raise RuntimeError(f"media container errored: {r.json()}")
        time.sleep(delay)
    # fall through — media_publish will surface a clear error if it's genuinely not ready


def publish_image(image_url: str, caption: str) -> dict:
    """Publish a single image with caption. Two calls: create container, then publish.
    Returns {media_id, permalink}.

    Failure semantics (load-bearing for not double-posting):
      - RuntimeError      → definitively NOT posted (clean error response / pre-publish
                            failure). Safe to retry.
      - PublishUncertain  → media_publish was sent but the response was lost; the post MAY
                            be live. Do NOT auto-retry; a human must reconcile."""
    token = _read_token()
    user_id = _user_id()
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        # 1) create media container (a timeout/error here = nothing posted, retryable)
        try:
            r = c.post(
                f"{GRAPH}/{API_VERSION}/{user_id}/media",
                data={"image_url": image_url, "caption": caption, "access_token": token},
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise RuntimeError(f"container create failed (not posted): {e}") from e
        _raise_ig(r)
        creation_id = r.json()["id"]

        # 2) wait for processing (photos: near-instant)
        _wait_ready(c, creation_id, token)

        # 3) publish. A transport/timeout error HERE is the dangerous case: the publish may
        #    have succeeded server-side while we lost the response -> uncertain, never retry.
        try:
            r = c.post(
                f"{GRAPH}/{API_VERSION}/{user_id}/media_publish",
                data={"creation_id": creation_id, "access_token": token},
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise PublishUncertain(
                f"media_publish sent but response lost (creation_id={creation_id}): {e}"
            ) from e
        _raise_ig(r)  # a clean >=400 here = definitely not published, retryable
        media_id = r.json()["id"]

        # 4) best-effort permalink fetch (never fatal)
        permalink = None
        try:
            pr = c.get(
                f"{GRAPH}/{API_VERSION}/{media_id}",
                params={"fields": "permalink", "access_token": token},
            )
            if pr.status_code == 200:
                permalink = pr.json().get("permalink")
        except Exception:
            pass

        return {"media_id": media_id, "permalink": permalink}


def refresh_token() -> dict:
    """Refresh the long-lived token (resets the 60-day clock). Persists the new token to the
    token file (atomically) and returns {expires_in, expires_days}."""
    token = _read_token()
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        r = c.get(
            f"{GRAPH}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
        _raise_ig(r)
        data = r.json()
    new = data.get("access_token")
    if not new:
        raise RuntimeError(f"refresh returned no token: {data}")
    _write_token(new)
    return {
        "access_token": "***stored***",
        "expires_in": data.get("expires_in"),
        "expires_days": round(data.get("expires_in", 0) / 86400, 1),
    }


# ---------- CLI ----------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "whoami"
    try:
        if cmd == "whoami":
            print(json.dumps(whoami(), indent=2))
        elif cmd == "refresh":
            print(json.dumps(refresh_token(), indent=2))
        else:
            print(f"unknown command: {cmd}\nusage: whoami | refresh")
            sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
