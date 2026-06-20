#!/usr/bin/env python3
# scripts/mandem/imagehost.py
# Upload a local image to a public HTTPS URL so Instagram's Graph API can fetch it
# (IG content publishing takes an image_url, not raw bytes). The object is temporary —
# the publish flow deletes it once the post is live.
#
# Backend: Cloudflare R2 (S3-compatible API via boto3). Config via env:
#   MANDEM_R2_ENDPOINT      https://<accountid>.r2.cloudflarestorage.com
#   MANDEM_R2_ACCESS_KEY
#   MANDEM_R2_SECRET
#   MANDEM_R2_BUCKET
#   MANDEM_R2_PUBLIC_BASE   the bucket's public base URL, e.g. https://pub-xxxx.r2.dev
#
# boto3 is required on the host: `pip3 install boto3`.
#
# Error hygiene: botocore exception text can embed the access-key id + endpoint + SigV4
# signing material. We never let raw botocore strings escape — callers get a generic
# "R2 <op> failed: <error-code>" so credentials never reach the agent / DB error column /
# Telegram / journald.

from __future__ import annotations

import mimetypes
import os
import sys
import uuid
from pathlib import Path

from . import _env

_REQUIRED = (
    "MANDEM_R2_ENDPOINT",
    "MANDEM_R2_ACCESS_KEY",
    "MANDEM_R2_SECRET",
    "MANDEM_R2_BUCKET",
    "MANDEM_R2_PUBLIC_BASE",
)


def configured() -> bool:
    """True if all R2 settings are present (so callers can fail fast with a clear message)."""
    _env.load()
    return all(os.environ.get(k) for k in _REQUIRED)


def _safe_code(exc: Exception) -> str:
    """A non-sensitive label for a botocore/other exception (no creds, no signing material)."""
    try:
        from botocore.exceptions import ClientError
        if isinstance(exc, ClientError):
            return str(exc.response.get("Error", {}).get("Code", "ClientError"))
    except Exception:
        pass
    return type(exc).__name__


def _client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:  # pragma: no cover - host dependency
        raise RuntimeError("boto3 not installed on this host — run `pip3 install boto3`") from e
    _env.load()
    return boto3.client(
        "s3",
        endpoint_url=_env.require("MANDEM_R2_ENDPOINT"),
        aws_access_key_id=_env.require("MANDEM_R2_ACCESS_KEY"),
        aws_secret_access_key=_env.require("MANDEM_R2_SECRET"),
        config=Config(signature_version="s3v4", region_name="auto"),
    )


def upload_public(local_path: str) -> dict:
    """Upload `local_path` to R2 under a random key. Returns {url, key, bucket}.
    `url` is publicly fetchable (MANDEM_R2_PUBLIC_BASE + key). Raises RuntimeError with a
    credential-free message on failure."""
    if not configured():
        raise RuntimeError(
            "R2 image hosting not configured — set MANDEM_R2_* in secrets.env "
            "(endpoint, access key, secret, bucket, public base)."
        )
    _env.load()
    bucket = _env.require("MANDEM_R2_BUCKET")
    base = _env.require("MANDEM_R2_PUBLIC_BASE").rstrip("/")
    p = Path(local_path)
    if not p.exists():
        raise FileNotFoundError(local_path)
    key = f"ig/{uuid.uuid4().hex}{p.suffix or '.png'}"
    ctype = mimetypes.guess_type(str(p))[0] or "image/png"
    try:
        _client().put_object(Bucket=bucket, Key=key, Body=p.read_bytes(), ContentType=ctype)
    except Exception as e:  # noqa: BLE001 — sanitize: never surface raw botocore text
        raise RuntimeError(f"R2 upload failed: {_safe_code(e)}") from None
    return {"url": f"{base}/{key}", "key": key, "bucket": bucket}


def delete(key: str) -> bool:
    """Best-effort delete of a previously-uploaded object (called after publish).
    Returns True on success, False (with a warning to stderr) on failure — so a failed
    cleanup is observable rather than a silent public-image leak. Never raises."""
    if not key or not configured():
        return False
    try:
        _env.load()
        _client().delete_object(Bucket=_env.require("MANDEM_R2_BUCKET"), Key=key)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[imagehost] WARN: R2 delete failed for key={key}: {_safe_code(e)}", file=sys.stderr)
        return False
