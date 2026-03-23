# VIX 警示通知器

這個專案會從富途牛牛的 `.VIX-US` 頁面擷取最新 VIX 報價，並在數值符合設定的門檻規則時，透過 Discord webhook 發送通知。

## 需求

- Python 3.13
- [`uv`](https://github.com/astral-sh/uv)

## 本機設定

```powershell
uv sync
```

執行前請先設定必要的環境變數：

```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

可選的門檻覆寫設定：

```powershell
$env:VIX_THRESHOLD=">=26"
```

支援的 `VIX_THRESHOLD` 格式：

- 比較運算規則：`>26`、`>=26`、`<26`、`<=26`、`==26`
- 不支援未帶比較運算子的純數字格式，例如 `30`
- 如果沒有設定 `VIX_THRESHOLD`，預設規則為 `>= 30`

執行通知程式：

```powershell
uv run python main.py
```

## 行為說明

- 從富途牛牛 [`.VIX-US` 頁面](https://www.futunn.com/hk/index/.VIX-US) 的內嵌 `window.__INITIAL_STATE__` 取得最新可用的 VIX 數值。
- 優先使用 `stock_info.price`。
- 如果 `stock_info.price` 不可用，會退回使用 `stock_charts_data.minuteChartsData.list` 內最後一筆可用的 `cc_price`。
- 將 `VIX_THRESHOLD` 解析為比較規則，只有在最新 VIX 符合規則時才發送 Discord 訊息。
- `VIX_THRESHOLD` 必須明確包含比較運算子，例如 `>=30`。
- 如果富途牛牛或 Discord 回傳錯誤，或缺少必要設定，程式會以非零狀態碼結束。

## 測試

```powershell
uv run pytest
```

## GitHub Actions

Workflow 檔案：`.github/workflows/vix-alert.yml`

- Action 觸發時間：週一到週五 `00:00 UTC` 與 `14:30 UTC`
- 台灣時間執行：週一到週五 `08:00 Asia/Taipei` 與 `22:30 Asia/Taipei`
- 支援透過 `workflow_dispatch` 手動觸發

Workflow 需要的 repository secret：

- `DISCORD_WEBHOOK_URL`

Workflow 支援的 repository variable：

- `VIX_THRESHOLD`

建議的 GitHub Actions 變數值：

```text
>=26
```

如果 GitHub Actions Variables 沒有設定 `VIX_THRESHOLD`，腳本會回退使用預設規則 `>= 30`。

排程 workflow 會在 `08:00 Asia/Taipei` 與 `22:30 Asia/Taipei` 直接執行通知程式。透過 `workflow_dispatch` 的手動執行也會直接開始執行。

Workflow 會先用 `uv sync --frozen` 安裝相依套件，然後執行：

```powershell
uv run python main.py
```
