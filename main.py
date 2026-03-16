from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from numbers import Real
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

YAHOO_VIX_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_THRESHOLD = 30.0

PriceFetcher = Callable[[], float]
WebhookSender = Callable[[str, str], None]


class NotifierError(RuntimeError):
    """Raised when the notifier cannot complete its work safely."""


def _is_real_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _format_price(value: float) -> str:
    return f"{value:.2f}"


def get_webhook_url(env: Mapping[str, str]) -> str:
    webhook_url = env.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise NotifierError("DISCORD_WEBHOOK_URL is required.")
    return webhook_url


def get_threshold(env: Mapping[str, str]) -> float:
    raw_value = env.get("VIX_THRESHOLD")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_THRESHOLD

    try:
        threshold = float(raw_value)
    except ValueError as exc:
        raise NotifierError("VIX_THRESHOLD must be a valid number.") from exc

    if not math.isfinite(threshold):
        raise NotifierError("VIX_THRESHOLD must be a finite number.")

    return threshold


def extract_latest_vix_price(payload: dict[str, Any]) -> float:
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise NotifierError("Yahoo Finance response is missing chart result data.") from exc

    meta = result.get("meta")
    if isinstance(meta, dict):
        regular_market_price = meta.get("regularMarketPrice")
        if _is_real_number(regular_market_price):
            return float(regular_market_price)

    try:
        close_values = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise NotifierError("Yahoo Finance response is missing VIX close prices.") from exc

    if not isinstance(close_values, list):
        raise NotifierError("Yahoo Finance close prices are not in the expected list format.")

    for value in reversed(close_values):
        if _is_real_number(value):
            return float(value)

    raise NotifierError("Yahoo Finance response does not include a usable latest VIX price.")


def fetch_latest_vix_price() -> float:
    request = Request(
        YAHOO_VIX_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise NotifierError(
            f"Failed to fetch VIX quote from Yahoo Finance: HTTP {exc.code}."
        ) from exc
    except URLError as exc:
        raise NotifierError(f"Failed to fetch VIX quote from Yahoo Finance: {exc.reason}.") from exc
    except json.JSONDecodeError as exc:
        raise NotifierError("Yahoo Finance returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise NotifierError("Yahoo Finance returned an unexpected JSON payload.")

    return extract_latest_vix_price(payload)


def build_alert_message(price: float, threshold: float) -> str:
    return f"VIX alert: {_format_price(price)} is above threshold {_format_price(threshold)}."


def send_discord_webhook(webhook_url: str, content: str) -> None:
    request = Request(
        webhook_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
    except HTTPError as exc:
        raise NotifierError(f"Discord webhook request failed: HTTP {exc.code}.") from exc
    except URLError as exc:
        raise NotifierError(f"Discord webhook request failed: {exc.reason}.") from exc

    if status is not None and not 200 <= status < 300:
        raise NotifierError(f"Discord webhook request failed: HTTP {status}.")


def run(
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    price_fetcher: PriceFetcher = fetch_latest_vix_price,
    webhook_sender: WebhookSender = send_discord_webhook,
) -> int:
    try:
        webhook_url = get_webhook_url(env)
        threshold = get_threshold(env)
        price = price_fetcher()
        print(
            f"Latest VIX price: {_format_price(price)}; threshold: {_format_price(threshold)}",
            file=stdout,
        )

        if price > threshold:
            webhook_sender(webhook_url, build_alert_message(price, threshold))
            print("Alert sent to Discord webhook.", file=stdout)
        else:
            print("Threshold not exceeded; no alert sent.", file=stdout)
    except NotifierError as exc:
        print(f"Error: {exc}", file=stderr)
        return 1

    return 0


def main() -> int:
    return run(os.environ, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
