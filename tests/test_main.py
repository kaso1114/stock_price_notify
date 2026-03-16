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
    def __init__(self, payload: object, status: int = 200) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

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
    operator = ">"

    for candidate in (">=", "<=", "==", ">", "<"):
        if stripped_threshold.startswith(candidate):
            operator = candidate
            stripped_threshold = stripped_threshold[len(candidate) :].strip()
            break

    return f"{operator} {float(stripped_threshold):.2f}"


def test_run_sends_webhook_when_price_matches_legacy_numeric_threshold() -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": "30"},
        price=31.25,
    )

    assert exit_code == 0
    assert stderr == ""
    assert calls == [
        (
            "https://example.test/webhook",
            "VIX alert: 31.25 matched threshold rule > 30.00.",
        )
    ]
    assert "Latest VIX price: 31.25; threshold rule: > 30.00" in stdout
    assert "Alert sent to Discord webhook." in stdout


def test_run_skips_webhook_when_legacy_numeric_threshold_is_not_matched() -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": "30"},
        price=29.95,
    )

    assert exit_code == 0
    assert calls == []
    assert stderr == ""
    assert "Threshold rule not matched; no alert sent." in stdout


def test_run_defaults_to_strict_greater_than_30_when_threshold_is_missing() -> None:
    exit_code, stdout, stderr, calls = run_notifier(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook"},
        price=30.0,
    )

    assert exit_code == 0
    assert calls == []
    assert stderr == ""
    assert "Latest VIX price: 30.00; threshold rule: > 30.00" in stdout
    assert "Threshold rule not matched; no alert sent." in stdout


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
        ("=>26", "valid comparison rule"),
        ("abc", "valid comparison rule"),
        (">=abc", "valid comparison rule"),
        ("nan", "finite number"),
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


def test_extract_latest_vix_price_falls_back_to_latest_non_null_close() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"regularMarketPrice": None},
                    "indicators": {
                        "quote": [
                            {
                                "close": [None, 27.5, None, 29.75],
                            }
                        ]
                    },
                }
            ]
        }
    }

    assert extract_latest_vix_price(payload) == pytest.approx(29.75)


def test_fetch_latest_vix_price_raises_for_yahoo_http_error() -> None:
    def failing_fetcher() -> float:
        raise NotifierError("Failed to fetch VIX quote from Yahoo Finance: HTTP 429.")

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


def test_fetch_latest_vix_price_raises_for_invalid_json_shape() -> None:
    def opener(request, timeout):
        return FakeResponse({"chart": {"result": []}})

    with pytest.raises(NotifierError, match="missing chart result data"):
        fetch_latest_vix_price_with_opener(opener)


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

    with pytest.raises(NotifierError, match="HTTP 503"):
        fetch_latest_vix_price_with_opener(opener)


def fetch_latest_vix_price_with_opener(opener) -> float:
    from unittest.mock import patch

    with patch("main.urlopen", opener):
        return fetch_latest_vix_price()


def send_discord_webhook_with_opener(opener) -> None:
    from unittest.mock import patch

    with patch("main.urlopen", opener):
        send_discord_webhook("https://example.test/webhook", "test payload")
