"""Backend API client: fetches live prices from the public CoinGecko API."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Iterable, Optional

log = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"


class PriceFetchError(Exception):
    """Raised when prices cannot be fetched after all retries."""


def _default_fetch(url: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "pricewatch/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CoinGeckoClient:
    """Small client with retry + exponential backoff.

    ``fetch`` and ``sleep`` are injectable so the client is easy to unit test.
    """

    def __init__(
        self,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 1.5,
        fetch: Optional[Callable[[str, float], dict]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._fetch = fetch or _default_fetch
        self._sleep = sleep

    def get_prices(self, coins: Iterable[str], vs: str = "usd") -> Dict[str, float]:
        coins = sorted({c.lower() for c in coins})
        if not coins:
            return {}
        query = urllib.parse.urlencode({"ids": ",".join(coins), "vs_currencies": vs})
        url = f"{BASE_URL}/simple/price?{query}"

        last_err: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                data = self._fetch(url, self.timeout)
                return self._parse(data, coins, vs)
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code not in (429, 500, 502, 503, 504):
                    break  # not retryable
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_err = exc
            if attempt < self.retries:
                delay = self.backoff ** attempt
                log.warning("Fetch failed (%s); retry %d/%d in %.1fs", last_err, attempt, self.retries, delay)
                self._sleep(delay)
        raise PriceFetchError(f"could not fetch prices for {coins}: {last_err}")

    @staticmethod
    def _parse(data: dict, coins: list, vs: str) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        for coin in coins:
            value = data.get(coin, {}).get(vs)
            if value is None:
                log.warning("No %s price returned for '%s' (unknown coin id?)", vs, coin)
                continue
            prices[coin] = float(value)
        return prices
