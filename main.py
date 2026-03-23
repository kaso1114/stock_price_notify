from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FUTUNN_VIX_URL = "https://www.futunn.com/hk/index/.VIX-US"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_THRESHOLD = 30.0
DEFAULT_THRESHOLD_OPERATOR = ">="
SUPPORTED_THRESHOLD_OPERATORS = (">=", "<=", "==", ">", "<")

PriceFetcher = Callable[[], float]
WebhookSender = Callable[[str, str], None]


class NotifierError(RuntimeError):
    """Raised when the notifier cannot complete its work safely."""


@dataclass(frozen=True)
class ThresholdRule:
    operator: str
    value: float


def _is_real_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _format_price(value: float) -> str:
    return f"{value:.2f}"


def _read_response_body(response: Any) -> str:
    try:
        raw_body = response.read()
    except OSError:
        return ""

    if isinstance(raw_body, bytes):
        body = raw_body.decode("utf-8", errors="replace")
    else:
        body = str(raw_body)

    body = body.strip()
    if not body:
        return ""

    if len(body) > 500:
        return f"{body[:497]}..."
    return body


def _format_response_body_suffix(body: str) -> str:
    if not body:
        return ""
    return f" Response body: {body}"


def _extract_embedded_state_json(document: str, marker: str) -> dict[str, Any]:
    marker_index = document.find(marker)
    if marker_index < 0:
        raise NotifierError("futunn page does not include embedded VIX state data.")

    json_start = document.find("{", marker_index + len(marker))
    if json_start < 0:
        raise NotifierError("futunn embedded VIX state is missing its JSON object.")

    depth = 0
    in_string = False
    escaped = False
    json_end: int | None = None

    for index in range(json_start, len(document)):
        character = document[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                json_end = index + 1
                break

    if json_end is None:
        raise NotifierError("futunn embedded VIX state JSON is incomplete.")

    try:
        payload = json.loads(document[json_start:json_end])
    except json.JSONDecodeError as exc:
        raise NotifierError("futunn embedded VIX state contains invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise NotifierError("futunn embedded VIX state is not a JSON object.")

    return payload


def get_webhook_url(env: Mapping[str, str]) -> str:
    webhook_url = env.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise NotifierError("DISCORD_WEBHOOK_URL is required.")
    return webhook_url


def get_threshold_rule(env: Mapping[str, str]) -> ThresholdRule:
    raw_value = env.get("VIX_THRESHOLD")
    if raw_value is None or not raw_value.strip():
        return ThresholdRule(DEFAULT_THRESHOLD_OPERATOR, DEFAULT_THRESHOLD)

    stripped_value = raw_value.strip()
    operator: str | None = None
    raw_threshold = stripped_value
    for candidate in SUPPORTED_THRESHOLD_OPERATORS:
        if stripped_value.startswith(candidate):
            operator = candidate
            raw_threshold = stripped_value[len(candidate) :].strip()
            break

    if operator is None:
        raise NotifierError("VIX_THRESHOLD must start with a comparison operator such as '>=30'.")

    if not raw_threshold:
        raise NotifierError("VIX_THRESHOLD must include a number after the comparison operator.")

    try:
        threshold = float(raw_threshold)
    except ValueError as exc:
        raise NotifierError("VIX_THRESHOLD must be a valid comparison rule like '>=26'.") from exc

    if not math.isfinite(threshold):
        raise NotifierError("VIX_THRESHOLD must use a finite number.")

    return ThresholdRule(operator, threshold)


def format_threshold_rule(rule: ThresholdRule) -> str:
    return f"{rule.operator} {_format_price(rule.value)}"


def matches_threshold_rule(price: float, rule: ThresholdRule) -> bool:
    if rule.operator == ">":
        return price > rule.value
    if rule.operator == ">=":
        return price >= rule.value
    if rule.operator == "<":
        return price < rule.value
    if rule.operator == "<=":
        return price <= rule.value
    if rule.operator == "==":
        return price == rule.value

    raise NotifierError(f"Unsupported VIX_THRESHOLD operator: {rule.operator}")


def extract_latest_vix_price(payload: dict[str, Any]) -> float:
    try:
        stock_info = payload["stock_info"]
    except KeyError as exc:
        raise NotifierError("futunn embedded state is missing stock_info.") from exc

    if not isinstance(stock_info, dict):
        raise NotifierError("futunn embedded stock_info is not an object.")

    price = stock_info.get("price")
    if isinstance(price, str):
        stripped_price = price.strip()
        if stripped_price and stripped_price != "--":
            try:
                return float(stripped_price)
            except ValueError:
                pass

    try:
        minute_chart_data = payload["stock_charts_data"]["minuteChartsData"]["list"]
    except (KeyError, TypeError) as exc:
        raise NotifierError("futunn embedded state is missing minute chart prices.") from exc

    if not isinstance(minute_chart_data, list):
        raise NotifierError("futunn minute chart prices are not in the expected list format.")

    for item in reversed(minute_chart_data):
        if not isinstance(item, dict):
            continue
        fallback_price = item.get("cc_price")
        if _is_real_number(fallback_price):
            return float(fallback_price)

    raise NotifierError("futunn embedded state does not include a usable latest VIX price.")


def fetch_latest_vix_price() -> float:
    request = Request(
        FUTUNN_VIX_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            document = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise NotifierError(f"Failed to fetch VIX quote from futunn: HTTP {exc.code}.") from exc
    except URLError as exc:
        raise NotifierError(f"Failed to fetch VIX quote from futunn: {exc.reason}.") from exc

    payload = _extract_embedded_state_json(document, "window.__INITIAL_STATE__=")

    return extract_latest_vix_price(payload)


def build_alert_message(price: float, rule: ThresholdRule) -> str:
    return f"VIX alert: {_format_price(price)} matched threshold rule {format_threshold_rule(rule)}."


def send_discord_webhook(webhook_url: str, content: str) -> None:
    request = Request(
        webhook_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None)
            response_body = _read_response_body(response)
    except HTTPError as exc:
        response_body = _read_response_body(exc)
        raise NotifierError(
            f"Discord webhook request failed: HTTP {exc.code}."
            f"{_format_response_body_suffix(response_body)}"
        ) from exc
    except URLError as exc:
        raise NotifierError(f"Discord webhook request failed: {exc.reason}.") from exc

    if status is not None and not 200 <= status < 300:
        raise NotifierError(
            f"Discord webhook request failed: HTTP {status}."
            f"{_format_response_body_suffix(response_body)}"
        )


def run(
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    price_fetcher: PriceFetcher = fetch_latest_vix_price,
    webhook_sender: WebhookSender = send_discord_webhook,
) -> int:
    try:
        webhook_url = get_webhook_url(env)
        threshold_rule = get_threshold_rule(env)
        price = price_fetcher()
        print(
            f"Latest VIX price: {_format_price(price)}; threshold rule: {format_threshold_rule(threshold_rule)}",
            file=stdout,
        )

        if matches_threshold_rule(price, threshold_rule):
            webhook_sender(webhook_url, build_alert_message(price, threshold_rule))
            print("Alert sent to Discord webhook.", file=stdout)
        else:
            print("Threshold rule not matched; no alert sent.", file=stdout)
    except NotifierError as exc:
        print(f"Error: {exc}", file=stderr)
        return 1

    return 0


def main() -> int:
    return run(os.environ, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
