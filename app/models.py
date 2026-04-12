from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class Watch:
    id: int
    chat_id: int
    network: str
    address: str
    label: str
    is_active: bool
    created_at: str
    updated_at: str
    last_cursor: Optional[str]
    last_checked_at: Optional[str]


@dataclass
class ChainEvent:
    id: str
    network: str
    address: str
    occurred_at: str
    summary: str
    explorer_url: Optional[str] = None
    tx_hash: Optional[str] = None
    direction: str = "related"
    counterparty: Optional[str] = None
    amount: Optional[str] = None
    asset: Optional[str] = None
    occurred_at_ts: Optional[int] = None
    amount_value: Decimal = Decimal("0")
