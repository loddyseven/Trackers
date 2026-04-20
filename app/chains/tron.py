from __future__ import annotations

import logging
from typing import List, Optional

import aiohttp

from app.models import ChainEvent
from app.utils import format_timestamp, format_units, parse_decimal, shorten_address

logger = logging.getLogger(__name__)


class TronGridClient:
    PAGE_LIMIT = 200
    KNOWN_TOKEN_DECIMALS = {
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": 6,  # Official USDT on TRON
    }

    def __init__(self, base_url: str, api_key: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def fetch_recent_activity(
        self,
        session: aiohttp.ClientSession,
        address: str,
        limit: int = 20,
    ) -> List[ChainEvent]:
        payload = await self._request_page(
            session=session,
            address=address,
            limit=min(limit, self.PAGE_LIMIT),
        )
        return self._parse_events(payload, address)[:limit]

    async def fetch_all_activity(
        self,
        session: aiohttp.ClientSession,
        address: str,
    ) -> List[ChainEvent]:
        events: List[ChainEvent] = []
        fingerprint: Optional[str] = None

        while True:
            payload = await self._request_page(
                session=session,
                address=address,
                limit=self.PAGE_LIMIT,
                fingerprint=fingerprint,
            )
            page_events = self._parse_events(payload, address)
            if not page_events:
                break

            events.extend(page_events)
            meta = payload.get("meta") or {}
            fingerprint = meta.get("fingerprint")
            if not fingerprint or len(page_events) < self.PAGE_LIMIT:
                break

        return events

    async def _request_page(
        self,
        session: aiohttp.ClientSession,
        address: str,
        limit: int,
        fingerprint: Optional[str] = None,
    ) -> dict:
        url = "{0}/v1/accounts/{1}/transactions/trc20".format(self.base_url, address)
        headers = {}
        if self.api_key:
            headers["TRON-PRO-API-KEY"] = self.api_key
        params = {
            "limit": limit,
            "only_confirmed": "true",
        }
        if fingerprint:
            params["fingerprint"] = fingerprint
        async with session.get(url, headers=headers, params=params) as response:
            response.raise_for_status()
            return await response.json()

    def _parse_events(self, payload: dict, address: str) -> List[ChainEvent]:
        events = []
        for item in payload.get("data", []):
            tx_id = item.get("transaction_id")
            if not tx_id:
                continue

            token_info = item.get("token_info") or {}
            decimals = self._resolve_decimals(token_info)
            amount = format_units(item.get("value"), decimals)
            symbol = token_info.get("symbol") or token_info.get("name") or "TRC20"
            from_address = item.get("from", "unknown")
            to_address = item.get("to", "unknown")
            if to_address == address:
                direction = "incoming"
                counterparty = from_address
            elif from_address == address:
                direction = "outgoing"
                counterparty = to_address
            else:
                direction = "related"
                counterparty = to_address

            summary = "{0} {1} {2} with {3}".format(
                direction.capitalize(),
                amount,
                symbol,
                shorten_address(counterparty),
            )
            occurred_at_ts = int((item.get("block_timestamp", 0) or 0) / 1000)
            events.append(
                ChainEvent(
                    id=tx_id,
                    network="trc20",
                    address=address,
                    occurred_at=format_timestamp(occurred_at_ts),
                    summary=summary,
                    explorer_url="https://tronscan.org/#/transaction/{0}".format(tx_id),
                    tx_hash=tx_id,
                    direction=direction,
                    counterparty=counterparty,
                    amount=amount,
                    asset=symbol,
                    occurred_at_ts=occurred_at_ts,
                    amount_value=parse_decimal(amount),
                )
            )
        return events

    def _resolve_decimals(self, token_info: dict) -> Optional[int]:
        contract_address = token_info.get("address")
        reported = token_info.get("decimals")

        if contract_address in self.KNOWN_TOKEN_DECIMALS:
            expected = self.KNOWN_TOKEN_DECIMALS[contract_address]
            if reported not in (None, "", expected, str(expected)):
                logger.warning(
                    "Overriding suspicious decimals for %s: reported=%s expected=%s",
                    contract_address,
                    reported,
                    expected,
                )
            return expected

        symbol = str(token_info.get("symbol") or "").upper()
        name = str(token_info.get("name") or "").strip().lower()
        if symbol == "USDT" and name == "tether usd":
            return 6

        return reported
