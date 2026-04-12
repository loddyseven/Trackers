from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_timestamp(value: Optional[int]) -> str:
    if not value:
        return "unknown time"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def shorten_address(address: str, head: int = 6, tail: int = 6) -> str:
    if len(address) <= head + tail + 3:
        return address
    return "{0}...{1}".format(address[:head], address[-tail:])


def format_units(raw_value: Optional[str], decimals: Optional[int], default: str = "0") -> str:
    if raw_value in (None, ""):
        return default
    try:
        value = Decimal(str(raw_value))
        if decimals:
            value = value / (Decimal(10) ** int(decimals))
        normalized = value.normalize()
        text = format(normalized, "f")
        return text.rstrip("0").rstrip(".") or "0"
    except (InvalidOperation, ValueError):
        return str(raw_value)


def format_ton_amount(raw_value: Optional[str]) -> str:
    return format_units(raw_value, 9, default="0")


def parse_decimal(raw_value: Optional[str], default: str = "0") -> Decimal:
    if raw_value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def format_decimal(value: Decimal, default: str = "0") -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") or default


def sanitize_filename(value: str, default: str = "wallet") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or default
