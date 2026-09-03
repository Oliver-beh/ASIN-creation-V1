"""Pure, deterministic transforms from BDF source values to Amazon values.

Every function here is total and side-effect free: given the same input it always
returns the same output, and when it cannot produce a confident answer it returns
None so the caller can flag the field rather than guess.
"""

from __future__ import annotations

import datetime as _dt
import re

# ------------------------------------------------------------------ identifiers


def gtin14(value) -> str | None:
    """Left-pad a GTIN/EAN to 14 digits.

    BDF ships 8-, 12- or 13-digit EANs; Amazon wants 14 with leading zeros.
    An 8-digit EAN therefore gains 6 zeros, a 13-digit one gains 1.
    """
    if value in (None, ""):
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits or len(digits) > 14:
        return None
    return digits.zfill(14)


def gtin_check_digit_ok(gtin: str) -> bool:
    """GS1 mod-10 check. Used for QA only - a bad check digit is flagged, not fixed."""
    if not gtin or not gtin.isdigit() or len(gtin) not in (8, 12, 13, 14):
        return False
    body, check = gtin[:-1], int(gtin[-1])
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


# ---------------------------------------------------------------- measurements

_UNIT_ALIASES = {
    "g": "g",
    "gr": "g",
    "gramm": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kilo": "kg",
    "kilogramm": "kg",
    "ml": "ml",
    "milliliter": "ml",
    "millilitre": "ml",
    "l": "l",
    "liter": "l",
    "st": "st",
    "stk": "st",
    "stueck": "st",
    "stück": "st",
    "pcs": "st",
}

_MEASURE_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-ZäöüÄÖÜ]*)\s*$")


def parse_measure(value, default_unit=None):
    """'122 g' -> (122.0, 'g');  '0,12 kg' -> (0.12, 'kg');  1.14 -> (1.14, default).

    Returns (None, None) when the value cannot be read. The unit is never
    invented: a bare number only gets `default_unit`, which the caller supplies
    from the column's documented semantics.
    """
    if value in (None, ""):
        return None, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), default_unit
    m = _MEASURE_RE.match(str(value))
    if not m:
        return None, None
    number = float(m.group(1).replace(",", "."))
    unit = _UNIT_ALIASES.get(m.group(2).strip().lower()) if m.group(2).strip() else default_unit
    return number, unit


def to_number(value):
    """Currency-ish or German-decimal text to float. None when unreadable."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")  # 1.234,56
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def to_int(value):
    n = to_number(value)
    return int(round(n)) if n is not None else None


def to_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    s = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def round_money(value, places=2):
    n = to_number(value)
    return round(n, places) if n is not None else None


# ------------------------------------------------------------------ item name

_PACK_SUFFIX_RE = re.compile(r"\s+1\s*(ST|STK|STÜCK|PCS)\s*$", re.I)


def clean_title(title):
    """Strip BDF's internal ' 1 ST' pack suffix and a duplicated leading brand token.

    Both patterns appear in the gift-set sheet ('NIVEA Feel Good Geschenkset 1 ST',
    'NIVEA NIVEA Adventskalender Female 2026 1 ST'). Everything else is left
    exactly as BDF supplied it - the April batch shows 58/58 titles copied verbatim,
    so this must not be a general-purpose rewriter.
    """
    if not title:
        return None, []
    notes = []
    out = str(title).strip()

    stripped = _PACK_SUFFIX_RE.sub("", out)
    if stripped != out:
        notes.append("removed trailing pack-count suffix")
        out = stripped

    tokens = out.split()
    if len(tokens) >= 2 and tokens[0].casefold() == tokens[1].casefold():
        out = " ".join(tokens[1:])
        notes.append(f"removed duplicated leading token '{tokens[0]}'")

    out = re.sub(r"\s{2,}", " ", out).strip()
    return out, notes


def brand_from_title(title, brand_rules, gender=None):
    """Resolve the brand from the item name.

    brand_rules is an ordered list of {match: [...], brand: X, requires: [...]}.
    Verified against 58 rows: the brand is always the first title token, with
    NIVEA splitting into NIVEA / NIVEA MEN / NIVEA SUN on a later token.
    """
    if not title:
        return None
    upper = f" {str(title).upper()} "
    first = str(title).split()[0].upper() if title.split() else ""
    for rule in brand_rules:
        if first not in [m.upper() for m in rule.get("match", [])]:
            continue
        requires = rule.get("requires")
        requires_gender = rule.get("requires_gender")
        if requires or requires_gender:
            by_title = requires and any(f" {r.upper()} " in upper for r in requires)
            by_gender = requires_gender and gender and str(gender).strip() in requires_gender
            if not (by_title or by_gender):
                continue
        return rule["brand"]
    return None


def contains_any(text, needles):
    if not text:
        return False
    hay = f" {str(text).lower()} "
    return any(f"{n.lower()}" in hay for n in needles)
