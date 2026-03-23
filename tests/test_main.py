from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from main import (
    NotifierError,
    extract_latest_vix_price,
    fetch_latest_vix_price,
    run,
    send_discord_webhook,
)


class FakeResponse:
    def __init__(self, payload: str | bytes, status: int = 200) -> None:
        self.status = status
        if isinstance(payload, bytes):
            self._payload = payload
        else:
            self._payload = payload.encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._payload
        return self._payload[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def run_notifier(
    env: dict[str, str],
    *,
    price: float,
) -> tuple[int, str, str, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        env,
        stdout,
        stderr,
        price_fetcher=lambda: price,
        webhook_sender=lambda url, content: calls.append((url, content)),
    )

    return exit_code, stdout.getvalue(), stderr.getvalue(), calls


def format_rule_text(raw_threshold: str) -> str:
    stripped_threshold = raw_threshold.strip()
    operator = ""

    for candidate in (">=", "<=", "==", ">", "<"):
        if stripped_threshold.startswith(candidate):
            operator = candidate
            stripped_threshold = stripped_threshold[len(candidate) :].strip()
            break

    if not operator:
        raise ValueError(f"Threshold is missing a comparison operator: {raw_threshold}")

    return f"{operator} {float(stripped_threshold):.2f}"


def build_futunn_html(state: object) -> str:
    return (
        "<html><head></head><body>"
        f'<script>window.__INITIAL_STATE__={json.dumps(state)};(function(){{}}());</script>'
        "</body></html>"
    )


def test_run_sends_webhook_when_price_matches_inclusive_default_threshold() -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook"},
        price=30.0,
    )

    assert exit_code == 0
    assert stderr == ""
    assert calls == [
        (
            "https://example.test/webhook",
            "VIX alert: 30.00 matched threshold rule >= 30.00.",
        )
    ]
    assert "Latest VIX price: 30.00; threshold rule: >= 30.00" in stdout
    assert "Alert sent to Discord webhook." in stdout


def test_run_skips_webhook_when_default_threshold_is_not_matched() -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook"},
        price=29.95,
    )

    assert exit_code == 0
    assert calls == []
    assert stderr == ""
    assert "Threshold rule not matched; no alert sent." in stdout


def test_run_rejects_legacy_numeric_threshold_without_operator() -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": "30"},
        price=30.0,
    )

    assert exit_code == 1
    assert calls == []
    assert stdout == ""
    assert "must start with a comparison operator" in stderr


@pytest.mark.parametrize(
    ("threshold", "expected_alert"),
    [
        (">26", False),
        (">=26", True),
        ("<26", False),
        ("<=26", True),
        ("==26", True),
    ],
)
def test_run_applies_operator_thresholds_at_exact_boundary(
    threshold: str,
    expected_alert: bool,
) -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": threshold},
        price=26.0,
    )

    assert exit_code == 0
    assert stderr == ""
    assert f"Latest VIX price: 26.00; threshold rule: {format_rule_text(threshold)}" in stdout
    assert bool(calls) == expected_alert


@pytest.mark.parametrize(
    ("threshold", "price"),
    [
        (">26", 26.1),
        ("<26", 25.9),
    ],
)
def test_run_supports_strict_operator_thresholds_away_from_boundary(
    threshold: str,
    price: float,
) -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": threshold},
        price=price,
    )

    assert exit_code == 0
    assert stderr == ""
    assert calls == [
        (
            "https://example.test/webhook",
            f"VIX alert: {price:.2f} matched threshold rule {format_rule_text(threshold)}.",
        )
    ]
    assert "Alert sent to Discord webhook." in stdout


@pytest.mark.parametrize(
    ("threshold", "message"),
    [
        (">=", "include a number"),
        ("=>26", "start with a comparison operator"),
        ("abc", "start with a comparison operator"),
        (">=abc", "valid comparison rule"),
        ("nan", "start with a comparison operator"),
        (">=inf", "finite number"),
    ],
)
def test_run_returns_nonzero_for_invalid_threshold_rules(
    threshold: str,
    message: str,
) -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": threshold},
        price=26.0,
    )

    assert exit_code == 1
    assert calls == []
    assert stdout == ""
    assert message in stderr


def test_extract_latest_vix_price_prefers_stock_info_price() -> None:
    payload = {
        "stock_info": {"price": "30.200"},
        "stock_charts_data": {
            "minuteChartsData": {
                "list": [
                    {"cc_price": 29.75},
                ]
            }
        },
    }

    assert extract_latest_vix_price(payload) == pytest.approx(30.2)


def test_extract_latest_vix_price_falls_back_to_latest_minute_chart_price() -> None:
    payload = {
        "stock_info": {"price": "--"},
        "stock_charts_data": {
            "minuteChartsData": {
                "list": [
                    {"cc_price": None},
                    {"cc_price": 29.75},
                    {"cc_price": 30.18},
                ]
            }
        },
    }

    assert extract_latest_vix_price(payload) == pytest.approx(30.18)


def test_fetch_latest_vix_price_raises_for_futunn_http_error() -> None:
    def failing_fetcher() -> float:
        raise NotifierError("Failed to fetch VIX quote from futunn: HTTP 429.")

    calls: list[tuple[str, str]] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook"},
        stdout,
        stderr,
        price_fetcher=failing_fetcher,
        webhook_sender=lambda url, content: calls.append((url, content)),
    )

    assert exit_code == 1
    assert calls == []
    assert "HTTP 429" in stderr.getvalue()


def test_fetch_latest_vix_price_raises_when_embedded_state_is_missing() -> None:
    def opener(request, timeout):
        return FakeResponse("<html><body>missing embedded state</body></html>")

    with pytest.raises(NotifierError, match="does not include embedded VIX state data"):
        fetch_latest_vix_price_with_opener(opener)


def test_fetch_latest_vix_price_raises_when_embedded_state_json_is_invalid() -> None:
    def opener(request, timeout):
        return FakeResponse('<script>window.__INITIAL_STATE__={"stock_info": ;</script>')

    with pytest.raises(NotifierError, match="JSON is incomplete|contains invalid JSON"):
        fetch_latest_vix_price_with_opener(opener)


def test_fetch_latest_vix_price_raises_when_embedded_state_shape_is_invalid() -> None:
    def opener(request, timeout):
        return FakeResponse(build_futunn_html({"stock_info": {}, "stock_charts_data": {}}))

    with pytest.raises(NotifierError, match="missing minute chart prices"):
        fetch_latest_vix_price_with_opener(opener)


def test_fetch_latest_vix_price_parses_embedded_state_html() -> None:
    def opener(request, timeout):
        assert request.headers["User-agent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        assert "text/html" in request.headers["Accept"]
        return FakeResponse(
            build_futunn_html(
                {
                    "stock_info": {"price": "30.200"},
                    "stock_charts_data": {
                        "minuteChartsData": {
                            "list": [
                                {"cc_price": 30.18},
                            ]
                        }
                    },
                }
            )
        )

    assert fetch_latest_vix_price_with_opener(opener) == pytest.approx(30.2)


def test_run_returns_nonzero_when_discord_webhook_fails() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def failing_sender(url: str, content: str) -> None:
        raise NotifierError("Discord webhook request failed: HTTP 500.")

    exit_code = run(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook"},
        stdout,
        stderr,
        price_fetcher=lambda: 31.5,
        webhook_sender=failing_sender,
    )

    assert exit_code == 1
    assert "HTTP 500" in stderr.getvalue()


def test_send_discord_webhook_includes_http_error_body() -> None:
    def opener(request, timeout):
        assert request.headers["User-agent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        assert request.headers["Accept"] == "application/json"
        assert request.headers["Content-type"] == "application/json"
        body = io.BytesIO(b'{"message":"Unknown Webhook","code":10015}')
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=body)

    with pytest.raises(NotifierError, match='HTTP 403. Response body: {"message":"Unknown Webhook","code":10015}'):
        send_discord_webhook_with_opener(opener)


def test_fetch_latest_vix_price_wraps_http_error() -> None:
    def opener(request, timeout):
        raise HTTPError(request.full_url, 503, "Service Unavailable", hdrs=None, fp=None)

    with pytest.raises(NotifierError, match="futunn: HTTP 503"):
        fetch_latest_vix_price_with_opener(opener)


def fetch_latest_vix_price_with_opener(opener) -> float:
    from unittest.mock import patch

    with patch("main.urlopen", opener):
        return fetch_latest_vix_price()


def send_discord_webhook_with_opener(opener) -> None:
    from unittest.mock import patch

    with patch("main.urlopen", opener):
        send_discord_webhook("https://example.test/webhook", "test payload")
