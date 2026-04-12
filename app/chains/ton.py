from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional
from urllib.parse import quote

import aiohttp

from app.models import ChainEvent
from app.utils import format_timestamp, format_ton_amount, format_units, parse_decimal, shorten_address


class TonApiClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def fetch_recent_activity(
        self,
        session: aiohttp.ClientSession,
        address: str,
        limit: int = 20,
    ) -> List[ChainEvent]:
        url = "{0}/v2/accounts/{1}/events".format(self.base_url, quote(address, safe=""))
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer {0}".format(self.api_key)
        async with session.get(url, headers=headers, params={"limit": limit}) as response:
            response.raise_for_status()
            payload = await response.json()

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
