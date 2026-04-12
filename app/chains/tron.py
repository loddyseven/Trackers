from __future__ import annotations

from typing import List, Optional

import aiohttp

from app.models import ChainEvent
from app.utils import format_timestamp, format_units, parse_decimal, shorten_address


class TronGridClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def fetch_recent_activity(
        self,
        session: aiohttp.ClientSession,
        address: str,
        limit: int = 20,
    ) -> List[ChainEvent]:
        url = "{0}/v1/accounts/{1}/transactions/trc20".format(self.base_url, address)
        headers = {}
        if self.api_key:
            headers["TRON-PRO-API-KEY"] = self.api_key
        params = {
            "limit": limit,
            "only_confirmed": "true",
        }
        async with session.get(url, headers=headers, params=params) as response:
            response.raise_for_status()
            payload = await response.json()

        events = []
        for item in payload.get("data", []):
            tx_id = item.get("transaction_id")
            if not tx_id:
                continue

            token_info = item.get("token_info") or {}
            decimals = token_info.get("decimals")
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
