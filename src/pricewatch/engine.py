"""Alert engine: one cycle = fetch prices -> store -> evaluate rules -> notify."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .storage import Alert, Store

log = logging.getLogger(__name__)


@dataclass
class Triggered:
    alert: Alert
    price: float
    message: str


def evaluate(alert: Alert, price: float, previous: Optional[float]) -> Optional[str]:
    """Return a message if ``alert`` fires for ``price``, else None."""
    label = f"{alert.coin.upper()}/{alert.vs.upper()}"
    if alert.condition == "above" and price >= alert.threshold:
        return f"{label} is {price:,.2f}, at or above {alert.threshold:,.2f}"
    if alert.condition == "below" and price <= alert.threshold:
        return f"{label} is {price:,.2f}, at or below {alert.threshold:,.2f}"
    if alert.condition == "pct_change" and previous:
        change = (price - previous) / previous * 100
        if abs(change) >= alert.threshold:
            direction = "up" if change > 0 else "down"
            return (
                f"{label} moved {direction} {abs(change):.2f}% "
                f"({previous:,.2f} -> {price:,.2f}), threshold {alert.threshold:g}%"
            )
    return None


def run_cycle(
    store: Store,
    client,
    notifiers: Sequence,
    cooldown: float = 3600.0,
    now: Optional[float] = None,
) -> List[Triggered]:
    """Run one monitoring cycle and return the alerts that fired."""
    now = now if now is not None else time.time()
    alerts = store.list_alerts(active_only=True)
    if not alerts:
        log.info("No active alerts configured.")
        return []

    # group coins by quote currency so we make one API call per currency
    by_vs: Dict[str, set] = {}
    for a in alerts:
        by_vs.setdefault(a.vs, set()).add(a.coin)

    prices: Dict[tuple, float] = {}
    previous: Dict[tuple, Optional[float]] = {}
    for vs, coins in by_vs.items():
        fetched = client.get_prices(coins, vs)
        for coin, price in fetched.items():
            previous[(coin, vs)] = store.previous_price(coin, vs)
            store.record_price(coin, vs, price, ts=now)
            prices[(coin, vs)] = price

    fired: List[Triggered] = []
    for a in alerts:
        price = prices.get((a.coin, a.vs))
        if price is None:
            continue
        if a.last_triggered and now - a.last_triggered < cooldown:
            continue
        msg = evaluate(a, price, previous.get((a.coin, a.vs)))
        if msg:
            full = f"#{a.id} {msg}"
            for n in notifiers:
                n.send(full)
            store.mark_triggered(a.id, ts=now)
            fired.append(Triggered(a, price, full))
    log.info("Cycle complete: %d price(s) fetched, %d alert(s) fired", len(prices), len(fired))
    return fired
