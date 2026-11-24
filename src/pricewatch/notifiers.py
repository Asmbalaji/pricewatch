"""Notification channels. Each notifier exposes ``send(message)``."""
from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


class ConsoleNotifier:
    def send(self, message: str) -> None:
        print(f"[ALERT] {message}", flush=True)


class FileNotifier:
    """Appends alerts to a log file."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, message: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")


class WebhookNotifier:
    """POSTs JSON to a webhook. The payload carries both ``text`` (Slack)
    and ``content`` (Discord) so either service accepts it."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        if not url.startswith(("https://", "http://")):
            raise ValueError("webhook url must start with http:// or https://")
        self.url = url
        self.timeout = timeout

    def send(self, message: str) -> None:
        body = json.dumps({"text": message, "content": message}).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except Exception as exc:  # never let a bad webhook crash the bot
            log.error("Webhook delivery failed: %s", exc)
