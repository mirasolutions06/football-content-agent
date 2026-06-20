# scripts/mandem/_env.py
# Tiny no-dep env loader.
# Search order: existing os.environ -> MANDEM_ENV_FILE -> ./.env (local dev).
# Idempotent + cached per process.

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SERVER_ENV_FILE = os.environ.get("MANDEM_ENV_FILE")
_LOCAL_DOTENV = _PROJECT_ROOT / ".env"

_loaded = False


def _parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load() -> None:
    """Load secrets into os.environ. Non-empty existing values win — does not overwrite."""
    global _loaded
    if _loaded:
        return
    paths = []
    if _SERVER_ENV_FILE:
        paths.append(Path(_SERVER_ENV_FILE).expanduser())
    paths.append(_LOCAL_DOTENV)
    for path in paths:
        for k, v in _parse_dotenv(path).items():
            # Only treat the env var as "set" if it has a non-empty value.
            # Some parent shells export keys as '' which would otherwise block the .env load.
            if v and not os.environ.get(k):
                os.environ[k] = v
    _loaded = True


def require(key: str) -> str:
    """Get an env var or raise a friendly error pointing at the right config."""
    load()
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"{key} is not set. Add it to {_LOCAL_DOTENV} for local dev, "
            "or point MANDEM_ENV_FILE at a server environment file. (.env is gitignored.)"
        )
    return val


def data_dir() -> Path:
    """Return the writable state directory for DBs, images, queues and token files."""
    load()
    raw = os.environ.get("MANDEM_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "mandem-fc"
