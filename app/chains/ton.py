from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Dict, List, Optional
import time
from urllib.parse import quote

import aiohttp

from app.models import ChainEvent
from app.utils import format_timestamp, format_ton_amount, format_units, parse_decimal, shorten_address


class TonApiClient:
    PAGE_LIMIT = 100
    DEFAULT_REQUEST_INTERVAL_SECONDS = 0.35
    DEFAULT_BACKOFF_SECONDS = 1.5
    MAX_RETRIES = 3

    def __init__(self, base_url: str, api_key: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._request_lock = asyncio.Lock()
        self._next_request_ready_at = 0.0

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
        before_lt: Optional[int] = None

        while True:
            payload = await self._request_page(
                session=session,
                address=address,
                limit=self.PAGE_LIMIT,
                before_lt=before_lt,
            )
            page_events = self._parse_events(payload, address)
            if not page_events:
                break

            events.extend(page_events)
            next_from = payload.get("next_from")
            if not next_from or int(next_from) == 0 or len(page_events) < self.PAGE_LIMIT:
                break
            before_lt = int(next_from)

        return events

    async def _request_page(
        self,
        session: aiohttp.ClientSession,
        address: str,
        limit: int,
        before_lt: Optional[int] = None,
    ) -> dict:
        url = "{0}/v2/accounts/{1}/events".format(self.base_url, quote(address, safe=""))
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer {0}".format(self.api_key)
        params = {"limit": limit}
        if before_lt is not None:
            params["before_lt"] = before_lt
        for attempt in range(self.MAX_RETRIES):
            await self._wait_for_request_slot()
            async with session.get(url, headers=headers, params=params) as response:
                if response.status != 429:
                    response.raise_for_status()
                    return await response.json()

                if attempt == self.MAX_RETRIES - 1:
                    response.raise_for_status()

                retry_after = self._parse_retry_after(response)
            await asyncio.sleep(retry_after)

        raise RuntimeError("TONAPI retries exhausted")

    async def _wait_for_request_slot(self) -> None:
        async with self._request_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._next_request_ready_at - now)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._next_request_ready_at = (
                time.monotonic() + self.DEFAULT_REQUEST_INTERVAL_SECONDS
            )

    def _parse_retry_after(self, response: aiohttp.ClientResponse) -> float:
        header = (response.headers.get("Retry-After") or "").strip()
        if header.isdigit():
            return max(float(header), self.DEFAULT_BACKOFF_SECONDS)
        return self.DEFAULT_BACKOFF_SECONDS

    def _parse_events(self, payload: Dict, address: str) -> List[ChainEvent]:
        events = []
        for item in payload.get("events", []):
            event_id = item.get("event_id") or str(item.get("lt") or "")
            actions = item.get("actions") or []
            summary = self._build_summary(item)
            primary = self._extract_primary_details(actions, address)
            tx_hash = self._extract_transaction_hash(actions)
            occurred_at_ts = int(item.get("timestamp") or 0)
            events.append(
                ChainEvent(
                    id=event_id,
                    network="ton",
                    address=address,
                    occurred_at=format_timestamp(occurred_at_ts),
                    summary=summary,
                    explorer_url=(
                        "https://tonviewer.com/transaction/{0}".format(tx_hash) if tx_hash else None
                    ),
                    tx_hash=tx_hash,
                    direction=primary["direction"],
                    counterparty=primary["counterparty"],
                    amount=primary["amount"],
                    asset=primary["asset"],
                    occurred_at_ts=occurred_at_ts,
                    amount_value=primary["amount_value"],
                )
            )
        return events

    def _build_summary(self, item: Dict) -> str:
        actions = item.get("actions") or []
        if not actions:
            return "New TON activity detected"

        rendered = []
        for action in actions[:3]:
            rendered.append(self._summarize_action(action))
        return " | ".join(part for part in rendered if part)

    def _summarize_action(self, action: Dict) -> str:
        action_type = action.get("type", "Unknown")
        payload = action.get(action_type) or {}

        if action_type == "TonTransfer":
            amount = format_ton_amount(payload.get("amount"))
            sender = self._extract_account(payload.get("sender"))
            recipient = self._extract_account(payload.get("recipient"))
            return "TON transfer {0} TON {1} -> {2}".format(
                amount,
                shorten_address(sender),
                shorten_address(recipient),
            )

        if action_type == "JettonTransfer":
            jetton = payload.get("jetton", {}) or {}
            amount = format_units(payload.get("amount"), jetton.get("decimals"), default="?")
            symbol = jetton.get("symbol") or jetton.get("name") or "JETTON"
            sender = self._extract_account(payload.get("sender"))
            recipient = self._extract_account(payload.get("recipient"))
            return "Jetton {0} {1} {2} -> {3}".format(
                amount,
                symbol,
                shorten_address(sender),
                shorten_address(recipient),
            )

        if action_type == "NftItemTransfer":
            nft = payload.get("nft") or {}
            name = nft.get("name") or shorten_address((nft.get("address") or "NFT"))
            recipient = self._extract_account(payload.get("recipient"))
            return "NFT transfer {0} -> {1}".format(name, shorten_address(recipient))

        return action_type

    def _extract_primary_details(self, actions: List[Dict], address: str) -> Dict:
        for action in actions:
            details = self._extract_action_details(action, address)
            if details:
                return details

        return {
            "direction": "related",
            "counterparty": None,
            "amount": None,
            "asset": None,
            "amount_value": Decimal("0"),
        }

    @staticmethod
    def _extract_transaction_hash(actions: List[Dict]) -> Optional[str]:
        for action in actions:
            hashes = action.get("base_transactions") or []
            if hashes:
                return hashes[0]
        return None

    def _extract_action_details(self, action: Dict, address: str) -> Optional[Dict]:
        action_type = action.get("type", "Unknown")
        payload = action.get(action_type) or {}

        if action_type == "TonTransfer":
            sender = self._extract_account(payload.get("sender"))
            recipient = self._extract_account(payload.get("recipient"))
            amount = format_ton_amount(payload.get("amount"))
            direction = self._detect_direction(address, sender, recipient)
            counterparty = self._detect_counterparty(direction, sender, recipient)
            return {
                "direction": direction,
                "counterparty": counterparty,
                "amount": amount,
                "asset": "TON",
                "amount_value": parse_decimal(amount),
            }

        if action_type == "JettonTransfer":
            jetton = payload.get("jetton", {}) or {}
            sender = self._extract_account(payload.get("sender"))
            recipient = self._extract_account(payload.get("recipient"))
            amount = format_units(payload.get("amount"), jetton.get("decimals"), default="0")
            direction = self._detect_direction(address, sender, recipient)
            counterparty = self._detect_counterparty(direction, sender, recipient)
            return {
                "direction": direction,
                "counterparty": counterparty,
                "amount": amount,
                "asset": jetton.get("symbol") or jetton.get("name") or "JETTON",
                "amount_value": parse_decimal(amount),
            }

        if action_type == "NftItemTransfer":
            sender = self._extract_account(payload.get("sender"))
            recipient = self._extract_account(payload.get("recipient"))
            nft = payload.get("nft") or {}
            direction = self._detect_direction(address, sender, recipient)
            counterparty = self._detect_counterparty(direction, sender, recipient)
            return {
                "direction": direction,
                "counterparty": counterparty,
                "amount": "1",
                "asset": nft.get("name") or "NFT",
                "amount_value": Decimal("0"),
            }

        return None

    @staticmethod
    def _detect_direction(address: str, sender: str, recipient: str) -> str:
        if recipient == address:
            return "incoming"
        if sender == address:
            return "outgoing"
        return "related"

    @staticmethod
    def _detect_counterparty(direction: str, sender: str, recipient: str) -> str:
        if direction == "incoming":
            return sender
        if direction == "outgoing":
            return recipient
        return recipient or sender

    @staticmethod
    def _extract_account(node: Optional[Dict]) -> str:
        if not node:
            return "unknown"
        return node.get("address") or node.get("name") or "unknown"
