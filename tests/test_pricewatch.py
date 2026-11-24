import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pricewatch.api import CoinGeckoClient, PriceFetchError  # noqa: E402
from pricewatch.cli import main  # noqa: E402
from pricewatch.engine import evaluate, run_cycle  # noqa: E402
from pricewatch.scheduler import run_forever  # noqa: E402
from pricewatch.storage import Store  # noqa: E402


class FakeClient:
    def __init__(self, *price_sequence):
        self.seq = list(price_sequence)

    def get_prices(self, coins, vs="usd"):
        prices = self.seq.pop(0)
        return {c: prices[c] for c in coins if c in prices}


class Collector:
    def __init__(self):
        self.messages = []

    def send(self, msg):
        self.messages.append(msg)


class StorageTests(unittest.TestCase):
    def test_add_list_remove(self):
        s = Store(":memory:")
        aid = s.add_alert("Bitcoin", "above", 100000)
        alerts = s.list_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].coin, "bitcoin")
        self.assertTrue(s.remove_alert(aid))
        self.assertFalse(s.remove_alert(aid))

    def test_validation(self):
        s = Store(":memory:")
        with self.assertRaises(ValueError):
            s.add_alert("bitcoin", "sideways", 1)
        with self.assertRaises(ValueError):
            s.add_alert("bitcoin", "above", -5)

    def test_price_history_and_stats(self):
        s = Store(":memory:")
        for i, p in enumerate([10.0, 20.0, 30.0]):
            s.record_price("bitcoin", "usd", p, ts=1000 + i)
        self.assertEqual(s.previous_price("bitcoin", "usd"), 30.0)
        stats = s.price_stats()[0]
        self.assertEqual((stats["n"], stats["lo"], stats["hi"], stats["avg"], stats["last"]), (3, 10.0, 30.0, 20.0, 30.0))


class EngineTests(unittest.TestCase):
    def test_above_and_below(self):
        s = Store(":memory:")
        s.add_alert("bitcoin", "above", 100)
        s.add_alert("ethereum", "below", 50)
        n = Collector()
        fired = run_cycle(s, FakeClient({"bitcoin": 120, "ethereum": 40}), [n], now=5000)
        self.assertEqual(len(fired), 2)
        self.assertEqual(len(n.messages), 2)

    def test_not_triggered(self):
        s = Store(":memory:")
        s.add_alert("bitcoin", "above", 100)
        n = Collector()
        self.assertEqual(run_cycle(s, FakeClient({"bitcoin": 90}), [n], now=5000), [])
        self.assertEqual(n.messages, [])

    def test_cooldown(self):
        s = Store(":memory:")
        s.add_alert("bitcoin", "above", 100)
        n = Collector()
        client = FakeClient({"bitcoin": 120}, {"bitcoin": 125}, {"bitcoin": 130})
        run_cycle(s, client, [n], cooldown=600, now=1000)
        run_cycle(s, client, [n], cooldown=600, now=1300)  # inside cooldown
        run_cycle(s, client, [n], cooldown=600, now=1700)  # after cooldown
        self.assertEqual(len(n.messages), 2)

    def test_pct_change(self):
        s = Store(":memory:")
        s.add_alert("bitcoin", "pct_change", 5)
        n = Collector()
        client = FakeClient({"bitcoin": 100}, {"bitcoin": 110})
        run_cycle(s, client, [n], now=1000)  # baseline, no previous price
        self.assertEqual(n.messages, [])
        run_cycle(s, client, [n], now=1100)
        self.assertEqual(len(n.messages), 1)
        self.assertIn("up 10.00%", n.messages[0])

    def test_evaluate_pure(self):
        s = Store(":memory:")
        s.add_alert("bitcoin", "below", 10)
        alert = s.list_alerts()[0]
        self.assertIsNotNone(evaluate(alert, 9, None))
        self.assertIsNone(evaluate(alert, 11, None))


class ApiTests(unittest.TestCase):
    def test_parse_success(self):
        c = CoinGeckoClient(fetch=lambda url, t: {"bitcoin": {"usd": 123.4}})
        self.assertEqual(c.get_prices(["bitcoin"]), {"bitcoin": 123.4})

    def test_retry_then_succeed(self):
        calls = {"n": 0}

        def flaky(url, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(url, 429, "rate limited", {}, None)
            return {"bitcoin": {"usd": 1}}

        c = CoinGeckoClient(fetch=flaky, sleep=lambda s: None)
        self.assertEqual(c.get_prices(["bitcoin"]), {"bitcoin": 1.0})
        self.assertEqual(calls["n"], 3)

    def test_gives_up(self):
        def boom(url, timeout):
            raise urllib.error.URLError("down")

        c = CoinGeckoClient(fetch=boom, retries=2, sleep=lambda s: None)
        with self.assertRaises(PriceFetchError):
            c.get_prices(["bitcoin"])

    def test_non_retryable_http_error(self):
        calls = {"n": 0}

        def bad(url, timeout):
            calls["n"] += 1
            raise urllib.error.HTTPError(url, 404, "nope", {}, None)

        c = CoinGeckoClient(fetch=bad, sleep=lambda s: None)
        with self.assertRaises(PriceFetchError):
            c.get_prices(["bitcoin"])
        self.assertEqual(calls["n"], 1)


class SchedulerTests(unittest.TestCase):
    def test_runs_max_cycles_and_survives_errors(self):
        calls = []

        def task():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")

        done = run_forever(task, interval=0, max_cycles=3, sleep=lambda s: None)
        self.assertEqual(done, 3)
        self.assertEqual(len(calls), 3)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def test_add_list_history_report_remove(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            code, out = self.run_cli("--db", db, "add", "bitcoin", "above", "100000")
            self.assertEqual(code, 0)
            self.assertIn("Added alert #1", out)
            _, out = self.run_cli("--db", db, "list")
            self.assertIn("bitcoin/usd above 100000", out)
            _, out = self.run_cli("--db", db, "history", "bitcoin")
            self.assertIn("No price history", out)
            code, out = self.run_cli("--db", db, "report", "--format", "csv")
            self.assertTrue(out.startswith("coin,vs,samples"))
            code, _ = self.run_cli("--db", db, "remove", "1")
            self.assertEqual(code, 0)

    def test_bad_input_returns_error(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            code, _ = self.run_cli("--db", db, "add", "bitcoin", "above", "-1")
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
