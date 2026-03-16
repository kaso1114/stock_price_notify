# VIX Alert Notifier

This project fetches the latest `^VIX` quote from Yahoo Finance and sends a Discord webhook alert when the value matches the configured threshold rule.

## Requirements

- Python 3.13
- [`uv`](https://github.com/astral-sh/uv)

## Local Setup

```powershell
uv sync
```

Set the required environment variable before running:

```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

Optional threshold override:

```powershell
$env:VIX_THRESHOLD=">=26"
```

Supported `VIX_THRESHOLD` formats:

- Comparator rules: `>26`, `>=26`, `<26`, `<=26`, `==26`
- Legacy bare numbers such as `30`, which still mean `> 30`
- If `VIX_THRESHOLD` is not set, the default rule is `> 30`

Run the notifier:

```powershell
uv run python main.py
```

## Behavior

- Fetches the latest available VIX value from Yahoo Finance.
- Uses `regularMarketPrice` first.
- Falls back to the latest non-null daily `close` if `regularMarketPrice` is unavailable.
- Parses `VIX_THRESHOLD` as a comparison rule and sends a Discord message only when the latest VIX matches that rule.
- Treats bare numeric values such as `30` as `> 30` for backward compatibility.
- Exits with a non-zero status if Yahoo Finance or Discord returns an error, or if required configuration is missing.

## Tests

```powershell
uv run pytest
```

## GitHub Actions

Workflow file: `.github/workflows/vix-alert.yml`

- Scheduled time: Monday to Friday, `01:00 UTC`
- Taiwan time: Monday to Friday, `09:00 Asia/Taipei`
- Manual trigger: supported through `workflow_dispatch`

Repository secret required by the workflow:

- `DISCORD_WEBHOOK_URL`

Repository variable supported by the workflow:

- `VIX_THRESHOLD`

Recommended GitHub Actions variable value:

```text
>=26
```

If `VIX_THRESHOLD` is not set in GitHub Actions Variables, the script falls back to the default rule `> 30`.

The workflow installs dependencies with `uv sync --frozen` and runs:

```powershell
uv run python main.py
```
