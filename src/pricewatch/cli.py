"""Command line interface for pricewatch."""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
from datetime import datetime
from typing import List, Optional

from . import __version__
from .api import CoinGeckoClient, PriceFetchError
from .engine import run_cycle
from .notifiers import ConsoleNotifier, FileNotifier, WebhookNotifier
from .scheduler import run_forever
from .storage import CONDITIONS, Store

DEFAULT_DB = os.environ.get("PRICEWATCH_DB", "~/.pricewatch/pricewatch.db")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pricewatch",
        description="Real-time crypto price alert bot.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite database path (env: PRICEWATCH_DB)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="add an alert rule")
    a.add_argument("coin", help="CoinGecko coin id, e.g. bitcoin, ethereum")
    a.add_argument("condition", choices=CONDITIONS)
    a.add_argument("threshold", type=float, help="price, or percent for pct_change")
    a.add_argument("--vs", default="usd", help="quote currency (default: usd)")

    sub.add_parser("list", help="list alert rules")

    r = sub.add_parser("remove", help="delete an alert rule")
    r.add_argument("id", type=int)

    for name, helptext in (("check", "run one monitoring cycle now"), ("run", "run continuously on a schedule")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--cooldown", type=float, default=3600, help="seconds before an alert can re-fire")
        s.add_argument("--webhook", help="Slack/Discord webhook URL for notifications")
        s.add_argument("--log-file", help="also append alerts to this file")
        if name == "run":
            s.add_argument("--interval", type=float, default=60, help="seconds between checks (default 60)")
            s.add_argument("--max-cycles", type=int, help="stop after N cycles (default: run forever)")

    h = sub.add_parser("history", help="show stored prices for a coin")
    h.add_argument("coin")
    h.add_argument("--vs", default="usd")
    h.add_argument("--limit", type=int, default=20)

    rep = sub.add_parser("report", help="summarise stored price data")
    rep.add_argument("--format", choices=("text", "markdown", "csv"), default="text")
    rep.add_argument("--output", help="write report to file instead of stdout")
    return p


def _notifiers(args) -> list:
    out: list = [ConsoleNotifier()]
    if args.log_file:
        out.append(FileNotifier(args.log_file))
    if args.webhook:
        out.append(WebhookNotifier(args.webhook))
    return out


def _render_report(rows, fmt: str) -> str:
    header = ["coin", "vs", "samples", "min", "max", "avg", "last"]
    data = [[r["coin"], r["vs"], r["n"], f"{r['lo']:.2f}", f"{r['hi']:.2f}", f"{r['avg']:.2f}", f"{r['last']:.2f}"] for r in rows]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(data)
        return buf.getvalue()
    if fmt == "markdown":
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(map(str, row)) + " |" for row in data]
        return "\n".join(lines) + "\n"
    widths = [max(len(str(x)) for x in col) for col in zip(header, *data)] if data else [len(h) for h in header]
    fmt_row = lambda row: "  ".join(str(c).ljust(w) for c, w in zip(row, widths))
    return "\n".join([fmt_row(header)] + [fmt_row(r) for r in data]) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    store = Store(args.db)
    try:
        if args.command == "add":
            aid = store.add_alert(args.coin, args.condition, args.threshold, args.vs)
            print(f"Added alert #{aid}: {args.coin} {args.condition} {args.threshold:g} ({args.vs})")

        elif args.command == "list":
            alerts = store.list_alerts()
            if not alerts:
                print("No alerts configured. Add one with: pricewatch add bitcoin above 100000")
            for a in alerts:
                last = datetime.fromtimestamp(a.last_triggered).isoformat(" ", "seconds") if a.last_triggered else "never"
                print(f"#{a.id:<3} {a.coin}/{a.vs} {a.condition} {a.threshold:g}  last fired: {last}")

        elif args.command == "remove":
            if store.remove_alert(args.id):
                print(f"Removed alert #{args.id}")
            else:
                print(f"No alert with id {args.id}", file=sys.stderr)
                return 1

        elif args.command == "check":
            try:
                run_cycle(store, CoinGeckoClient(), _notifiers(args), cooldown=args.cooldown)
            except PriceFetchError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2

        elif args.command == "run":
            client, notifiers = CoinGeckoClient(), _notifiers(args)
            print(f"Watching every {args.interval:g}s. Press Ctrl+C to stop.")
            try:
                run_forever(
                    lambda: run_cycle(store, client, notifiers, cooldown=args.cooldown),
                    interval=args.interval,
                    max_cycles=args.max_cycles,
                )
            except KeyboardInterrupt:
                print("\nStopped.")

        elif args.command == "history":
            rows = store.history(args.coin, args.vs, args.limit)
            if not rows:
                print("No price history yet.")
            for r in rows:
                ts = datetime.fromtimestamp(r["ts"]).isoformat(" ", "seconds")
                print(f"{ts}  {args.coin}/{args.vs}  {r['price']:,.2f}")

        elif args.command == "report":
            text = _render_report(store.price_stats(), args.format)
            if args.output:
                with open(args.output, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
                print(f"Report written to {args.output}")
            else:
                print(text, end="")
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
