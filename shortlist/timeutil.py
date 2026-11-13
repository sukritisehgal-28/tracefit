"""Date, number and money helpers.

Nothing here reads the system clock. "Today" always comes from configuration,
so results do not change depending on the day the tool is run.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

UTC = timezone.utc

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]
MONTH_RE = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
            r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
WEEKDAY_RE = r"(?:Mon|Tue(?:s)?|Wed(?:nes)?|Thu(?:rs)?|Fri|Sat(?:ur)?|Sun)(?:day)?"
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

NUMWORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60,
}
NUM_RE = r"(?:\d+(?:\.\d+)?|" + "|".join(sorted(NUMWORDS, key=len, reverse=True)) + r")"


def to_number(token: str) -> float:
    token = token.strip().lower()
    return float(NUMWORDS[token]) if token in NUMWORDS else float(token.replace(",", ""))


def month_of(token: str) -> int:
    return [m[:3] for m in _MONTH_NAMES].index(token.strip().lower()[:3]) + 1


def weekday_of(token: str) -> int:
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(token.strip().lower()[:3])


def parse_when(value: str, fmt: str | None = None) -> tuple[datetime, bool]:
    """Parse a source timestamp. Returns (UTC datetime, True if the source gave only a date)."""
    value = str(value).strip()
    if fmt:
        d = datetime.strptime(value, fmt)
        return datetime(d.year, d.month, d.day, tzinfo=UTC), True
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        d = date.fromisoformat(value)
        return datetime(d.year, d.month, d.day, tzinfo=UTC), True
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC), False


def next_month_day(month: int, day: int, anchor: date) -> date:
    """A month and day with no year means the next such date on or after the day it was said."""
    candidate = date(anchor.year, month, day)
    return candidate if candidate >= anchor else date(anchor.year + 1, month, day)


def parse_date_phrase(text: str, anchor: date) -> date:
    """'2026-12-01', 'December 1' or 'January 15, 2027'."""
    text = text.strip()
    if text[:4].isdigit() and "-" in text:
        return date.fromisoformat(text[:10])
    parts = text.replace(",", " ").split()
    month, day = month_of(parts[0]), int(parts[1])
    if len(parts) > 2 and parts[2].isdigit():
        return date(int(parts[2]), month, day)
    return next_month_day(month, day, anchor)


def parse_start_token(token: str) -> tuple[date, str, str]:
    """Start of a job from a résumé: '2025-05', 'May 2025' or '2016'.

    Returns (date, precision, label). A bare year is read conservatively as the
    end of that year, so experience is never overstated.
    """
    token = token.strip()
    if token[:4].isdigit() and len(token) >= 7 and token[4] == "-":
        y, m = int(token[:4]), int(token[5:7])
        return date(y, m, 1), "month", f"{_MONTH_NAMES[m - 1][:3].title()} {y}"
    parts = token.split()
    if len(parts) == 2:
        y, m = int(parts[1]), month_of(parts[0])
        return date(y, m, 1), "month", f"{_MONTH_NAMES[m - 1][:3].title()} {y}"
    y = int(token[:4])
    return date(y, 12, 31), "year", str(y)


def years_between(start: date, end: date) -> float:
    return max((end - start).days, 0) / 365.25


def calendar_years(start: date, end: date) -> float:
    """Years between two dates by the calendar, so 1 May 2024 to 1 May 2026 is exactly 2, leap years or not."""
    if end <= start:
        return 0.0
    months = (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)
    year, month = start.year + (start.month - 1 + months) // 12, (start.month - 1 + months) % 12 + 1
    anchor = date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
    return months / 12 + (end - anchor).days / 365.25


# Month names are spelled here, not asked of the C library: the output then never depends on the machine's
# language settings, and it stays fast in a forked worker process (on macOS, strftime after fork is slow).
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(d: date | datetime | None) -> str:
    if d is None:
        return "—"
    return f"{d.day} {MONTH_ABBR[d.month - 1]} {d.year}"


def fmt_span(years: float) -> str:
    months = int(round(years * 12))
    y, m = divmod(months, 12)
    if y and m:
        return f"{y} yr {m} mo"
    return f"{y} yr" if y else f"{m} mo"


def fmt_years(n: float) -> str:
    n = float(n)
    whole = int(n) if n == int(n) else n
    return f"{whole} year" + ("" if whole == 1 else "s")


def fmt_money(amount: float) -> str:
    return f"${amount:,.0f}" if float(amount) == int(amount) else f"${amount:,.2f}"


def fmt_pay(pay: dict) -> str:
    return f"{fmt_money(pay['amount'])}/{pay['unit']}"


def norm_unit(unit: str) -> str:
    unit = str(unit).strip().lower()
    return {"weekly": "week", "wk": "week", "hourly": "hour", "hr": "hour", "daily": "day"}.get(unit, unit)
