"""Pure, DB-free normalisation and validation helpers shared by the API, importer and ML layer."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

CHARGE_ID_RE = re.compile(r"ch_[A-Za-z0-9]{8,}")
CHANNELS = frozenset({"web", "app", "store"})
_TRUE = frozenset({"1", "true", "yes"})
_FALSE = frozenset({"0", "false", "no"})
_ISO_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalise_email(raw: str) -> str:
    """Trim and lowercase an email."""
    return raw.strip().lower()


def normalise_token(raw: str | None) -> str:
    """Trim and lowercase a free-text code (sku, channel...). ``None`` becomes ``""``."""
    return (raw or "").strip().lower()


def normalise_optional(raw: str | None) -> str | None:
    """Like :func:`normalise_token` but blank means "none"."""
    return normalise_token(raw) or None


def is_valid_charge_id(raw: object) -> bool:
    return isinstance(raw, str) and CHARGE_ID_RE.fullmatch(raw) is not None


def is_valid_email(email: str) -> bool:
    """Text, exactly one ``@``, non-empty both sides, no whitespace (input already normalised)."""
    if email.count("@") != 1 or any(ch.isspace() for ch in email):
        return False
    local, domain = email.split("@")
    return bool(local) and bool(domain)


def parse_iso_datetime(raw: str | None) -> datetime:
    """Parse an ISO 8601 date-time that carries ``Z`` or an offset; return it in UTC.

    Raises ``ValueError`` for anything else (naive, date-only, other formats, impossible dates).
    """
    value = (raw or "").strip()
    if not _ISO_DATETIME_RE.fullmatch(value):
        raise ValueError(f"not an ISO 8601 date-time with offset: {raw!r}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:  # defensive: regex already requires an offset
        raise ValueError("naive datetime")
    return parsed.astimezone(timezone.utc)


def parse_bool(raw: str | None) -> bool:
    """Accept 0/1/true/false/yes/no, case-insensitive; raise ``ValueError`` otherwise."""
    value = normalise_token(raw)
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def parse_query_date(raw: str) -> date:
    """Strict ``YYYY-MM-DD``; raise ``ValueError`` otherwise."""
    if not _DATE_RE.fullmatch(raw):
        raise ValueError(f"not YYYY-MM-DD: {raw!r}")
    return date.fromisoformat(raw)
