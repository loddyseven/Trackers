from __future__ import annotations

import re


TON_FRIENDLY_RE = re.compile(r"^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46,64}$")
TON_RAW_RE = re.compile(r"^-?\d+:[0-9a-fA-F]{64}$")
TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")

NETWORK_ALIASES = {
    "ton": "ton",
    "tron": "trc20",
    "trc20": "trc20",
    "trc-20": "trc20",
}


class ValidationError(ValueError):
    """User-facing validation error."""


def normalize_network(value: str) -> str:
    key = value.strip().lower()
    try:
        return NETWORK_ALIASES[key]
    except KeyError as exc:
        raise ValidationError("Поддерживаются только сети ton и trc20.") from exc


def normalize_address(network: str, value: str) -> str:
    address = value.strip()
    if network == "ton":
        if TON_FRIENDLY_RE.match(address) or TON_RAW_RE.match(address):
            return address
        raise ValidationError("TON-адрес выглядит некорректно.")
    if network == "trc20":
        if TRON_RE.match(address):
            return address
        raise ValidationError("TRC20/TRON-адрес должен начинаться с T и быть в формате Base58.")
    raise ValidationError("Неизвестная сеть.")


def normalize_label(value: str, address: str) -> str:
    label = value.strip()
    if not label or label == "-":
        return address
    return label[:80]
