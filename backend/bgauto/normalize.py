"""Parsers and normalisers for values found on scanned bank guarantees.

Every parser returns ``None`` when it is not sure. The pipeline never guesses:
a blank cell with a flag is preferred over a wrong value.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# OCR confusion maps. Digits are expected in some positions, letters in others.
_DIGIT_FIX = str.maketrans({
    "O": "0", "Q": "0", "I": "1", "L": "1", "|": "1", "!": "1",
    "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7",
})
_LETTER_FIX = str.maketrans({
    "0": "O", "1": "I", "5": "S", "8": "B", "6": "G", "2": "Z",
})


@dataclass
class Field:
    """One extracted tracker value plus how it was obtained."""

    value: object | None = None
    raw: str | None = None
    page: int | None = None
    confidence: float | None = None
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value not in (None, "") and not self.flags

    def flag(self, message: str) -> "Field":
        if message not in self.flags:
            self.flags.append(message)
        return self

    def note(self, message: str) -> "Field":
        if message not in self.notes:
            self.notes.append(message)
        return self


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fix_date_token(token: str) -> str:
    """'II' -> '11', 'O8' -> '08', 'l1' -> '11'."""
    return token.translate(_DIGIT_FIX)


def _month(token: str) -> int | None:
    t = token.upper().replace("0", "O").replace("1", "I").strip(".")
    if t in MONTHS:
        return MONTHS[t]
    if len(t) >= 3 and t[:3] in MONTHS:
        return MONTHS[t[:3]]
    return None


def parse_date(raw: str | None) -> dt.date | None:
    """Parse DD-MMM-YYYY, DD-MM-YYYY and Month DD, YYYY (with OCR noise)."""
    if not raw:
        return None
    text = squash(raw).upper()

    # 15-SEP-2025 / 11-0CT-2028 / 15 SEP 2025
    m = re.search(r"(\d{1,2}|[IL|!]{1,2})\s*[-/.]\s*([A-Z0-9]{3,4})\s*[-/.]\s*(\d{4}|\d{2})", text)
    if m:
        day = _fix_date_token(m.group(1))
        month = _month(m.group(2))
        year = m.group(3)
        if month and day.isdigit():
            try:
                return dt.date(int(year), month, int(day))
            except ValueError:
                return None

    # 06-04-2026 / 06.04.2026
    m = re.search(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})", text)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None

    # November 15, 2025
    m = re.search(r"([A-Z]{3,9})\s+(\d{1,2})\s*,?\s*(\d{4})", text)
    if m:
        month = _month(m.group(1))
        if month:
            try:
                return dt.date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                return None
    return None


def parse_amount(raw: str | None) -> float | None:
    """'INR 40, 78,080.00' -> 4078080.0 ; refuses anything not money-shaped."""
    if not raw:
        return None
    text = squash(raw).upper()
    m = re.search(r"(?:INR|RS\.?|RUPEES)?\s*((?:\d[\d,\s]{2,})\d(?:\.\d{1,2})?)", text)
    if not m:
        return None
    digits = re.sub(r"[,\s]", "", m.group(1))
    if not re.fullmatch(r"\d+(\.\d{1,2})?", digits):
        return None
    value = float(digits)
    return value if value > 0 else None


_UNITS = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6,
    "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "ELEVEN": 11,
    "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14, "FIFTEEN": 15,
    "SIXTEEN": 16, "SEVENTEEN": 17, "EIGHTEEN": 18, "NINETEEN": 19,
}
_TENS = {
    "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50,
    "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90,
}
_IGNORED = {"RUPEES", "RUPEE", "ONLY", "AND", "PAISE", "THE", "SUM", "OF"}


def amount_in_words_to_number(raw: str | None) -> int | None:
    """'RUPEES FORTY LAKH SEVENTY EIGHT THOUSAND EIGHTY ONLY' -> 4078080."""
    if not raw:
        return None
    total = 0
    current = 0
    seen = False
    for token in re.findall(r"[A-Z]+", raw.upper()):
        if token in _IGNORED:
            continue
        if token in _UNITS:
            current += _UNITS[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token == "HUNDRED":
            current = (current or 1) * 100
        elif token == "THOUSAND":
            total += (current or 1) * 1000
            current = 0
        elif token == "LAKH":
            total += (current or 1) * 100000
            current = 0
        elif token == "CRORE":
            total += (current or 1) * 10000000
            current = 0
        else:
            return None  # unknown word: do not trust the conversion
        seen = True
    return total + current if seen else None


def normalise_bg_no(raw: str | None) -> str | None:
    """Fix OCR digit/letter swaps and validate against the bank BG-number shape."""
    if not raw:
        return None
    token = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if len(token) < 10 or len(token) > 24:
        return None
    match = re.search(r"[A-Z]+", token)
    if not match:
        return None
    head = token[: match.start()].translate(_DIGIT_FIX)
    middle = match.group(0).translate(_LETTER_FIX)
    tail = token[match.end():].translate(_DIGIT_FIX)
    candidate = head + middle + tail
    if re.fullmatch(r"\d{3,6}[A-Z]{2,5}\d{6,14}", candidate):
        return candidate
    return None


def bg_no_from_filename(name: str) -> str | None:
    m = re.search(r"([0-9]{3,6}\s*[A-Z]{2,5}\s*[0-9]{6,14})", name.upper())
    if not m:
        return None
    return re.sub(r"\s", "", m.group(1))


# Character pairs Tesseract swaps in practice. Two tokens may only be treated as
# the same number if every difference falls inside one of these groups.
_CONFUSION_GROUPS = [
    {"0", "O", "Q"},
    {"1", "I", "L"},
    {"5", "S"},
    {"8", "B"},
    {"6", "G"},
    {"2", "Z"},
    {"7", "T"},
]


def _confusable(a: str, b: str) -> bool:
    return any(a in group and b in group for group in _CONFUSION_GROUPS)


def match_bg_no_hint(raw: str | None, hint: str | None) -> str | None:
    """Return ``hint`` when ``raw`` is the same length and differs only by OCR swaps.

    Used when OCR produced exactly one damaged token: OCR alone cannot tell
    ``2646T02252580004`` from ``2646TO2252580004``, but the filename can. The
    rule is deliberately strict - same length, every difference a known
    confusion pair - so it can never invent a different guarantee.
    """
    if not raw or not hint:
        return None
    token = re.sub(r"[^A-Z0-9]", "", raw.upper())
    target = re.sub(r"[^A-Z0-9]", "", hint.upper())
    if token == target:
        return target
    if len(token) != len(target):
        return None
    if all(a == b or _confusable(a, b) for a, b in zip(token, target)):
        return target
    return None


def normalise_bank(text: str) -> str | None:
    """Canonical issuing-bank name from a distinctive keyword in the text.

    The bank master starts empty and fills from confirmed BGs, so this cannot
    depend on the name already being in a list.
    """
    upper = text.upper()
    aliases = {
        "HDFC": "HDFC Bank",
        "ICICI": "ICICI Bank Limited",
        "PUNJAB NATIONAL": "Punjab National Bank",
        "PNB": "Punjab National Bank",
        "AXIS": "Axis Bank Limited",
        "YES BANK": "Yes Bank",
        "BANK OF BARODA": "Bank of Baroda",
        "AU SMALL": "AU SMALL FINANCE BANK",
    }
    for key, canonical in aliases.items():
        if key in upper:
            return canonical
    return None


def normalise_key(value: str) -> str:
    """Dedupe key: BG numbers ignore case and internal spaces."""
    return re.sub(r"\s+", "", value).upper()
