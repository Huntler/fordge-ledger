"""Small helpers: ULIDs, slugs, timestamps, and path containment."""

from __future__ import annotations

import os
import re
import secrets
import time
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def content_hash(text: str) -> str:
    """Cheap fingerprint for the stale-write guard (R10) — not a security
    hash, just enough to tell "the file changed under you" from "it didn't"
    without shipping the file's full content back on every save attempt.

    FNV-1a (32-bit), not SHA-256: this needs to be computed client-side too
    (forge-scad-editor's ScadWorkspace.tsx, before every Save), and
    `crypto.subtle` — the only SHA-256 available in a browser — refuses to
    run outside a secure context. This app is explicitly LAN/plain-HTTP
    (see README), so that path is unavailable in practice. A 32-bit
    fingerprint is more than enough for "did this exact file change under
    me" — a collision only ever causes a save that should have been
    blocked to go through, never data loss beyond what R10 already accepts
    (this is a guard, not a lock). Kept in sync with the identical
    implementation in forge-scad-editor's own utils.py and its frontend's
    lib/contentHash.ts.
    """
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


class StaleWriteError(ValueError):
    """Raised when a save's base_hash doesn't match the file's current
    on-disk content — someone else (another tab, another app) wrote to it
    since this buffer was loaded. Maps to 409, not 422/400 — see R10."""


def new_ulid() -> str:
    """Lexicographically sortable 26-char id. Stable across folder renames."""
    timestamp = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    value = (timestamp << 80) | randomness
    return "".join(_CROCKFORD[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def today() -> str:
    return date.today().isoformat()


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, fallback: str = "project") -> str:
    """`Desk Organizer!` -> `desk-organizer`. Used for folder names."""
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug or fallback


def unique_slug(base: str, taken: set[str]) -> str:
    """Append -2, -3 … until the slug is free."""
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    return f"{base}-{new_ulid()[-6:].lower()}"


def is_within(root: Path, candidate: Path) -> bool:
    """Guard against `..` escaping the library root."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def safe_join(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`, refusing anything that escapes it."""
    if os.path.isabs(relative):
        raise ValueError("absolute paths are not allowed")
    target = root / relative
    if not is_within(root, target):
        raise ValueError(f"path escapes the library root: {relative}")
    return target


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"
