"""Date-range parsing for resume roles."""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_POINT = r"(?:(?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2}|\d{1,2}/(?:19|20)\d{2})"
_PRESENT = r"present|current|now|today|ongoing"
RANGE = re.compile(
    rf"(?P<start>{_POINT})\s*(?:-|to|–|—)\s*(?P<end>{_POINT}|{_PRESENT})",
    re.IGNORECASE,
)


def parse_point(text: str, *, end: bool = False) -> date | None:
    """Parse ``Jan 2020``, ``January 2020``, ``01/2020`` or ``2020``.

    A bare year means January for a start and December for an end.
    """
    cleaned = text.strip().rstrip(".").lower()
    slash = re.fullmatch(r"(\d{1,2})/((?:19|20)\d{2})", cleaned)
    if slash:
        month, year = int(slash.group(1)), int(slash.group(2))
        return date(year, month, 1) if 1 <= month <= 12 else None
    named = re.fullmatch(r"([a-z]{3,9})\.?\s+((?:19|20)\d{2})", cleaned)
    if named:
        month_no = _MONTHS.get(named.group(1)[:3])
        return date(int(named.group(2)), month_no, 1) if month_no else None
    bare = re.fullmatch(r"(?:19|20)\d{2}", cleaned)
    if bare:
        return date(int(cleaned), 12 if end else 1, 1)
    return None


def parse_range(text: str) -> tuple[date, date | None, bool, str] | None:
    """Find a date range in ``text``.

    Returns ``(start, end, present, remainder)`` where ``remainder`` is the text with the range removed,
    or ``None`` if there is no parseable range.
    """
    match = RANGE.search(text)
    if not match:
        return None
    start = parse_point(match["start"])
    if start is None:
        return None
    raw_end = match["end"].strip().lower()
    present = bool(re.fullmatch(_PRESENT, raw_end))
    end = None if present else parse_point(raw_end, end=True)
    if end is None and not present:
        return None
    remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
    return start, end, present, remainder


def month_index(value: date) -> int:
    return value.year * 12 + value.month - 1
