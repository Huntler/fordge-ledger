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
