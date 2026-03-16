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


def test_run_sends_webhook_when_price_above_threshold() -> None:
    calls: list[tuple[str, str]] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": "30"},
        stdout,
        stderr,
        price_fetcher=lambda: 31.25,
        webhook_sender=lambda url, content: calls.append((url, content)),
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert calls == [
        (
            "https://example.test/webhook",
            "VIX alert: 31.25 is above threshold 30.00.",
        )
    ]
    assert "Latest VIX price: 31.25; threshold: 30.00" in stdout.getvalue()
    assert "Alert sent to Discord webhook." in stdout.getvalue()


def test_run_skips_webhook_when_price_at_or_below_threshold() -> None:
    calls: list[tuple[str, str]] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run(
        {"DISCORD_WEBHOOK_URL": "https://example.test/webhook", "VIX_THRESHOLD": "30"},
        stdout,
        stderr,
        price_fetcher=lambda: 29.95,
        webhook_sender=lambda url, content: calls.append((url, content)),
    )

    assert exit_code == 0
    assert calls == []
    assert stderr.getvalue() == ""
    assert "Threshold not exceeded; no alert sent." in stdout.getvalue()


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
