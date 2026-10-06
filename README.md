# RSI Oversold Scanner → Telegram

A small Python bot that scans **all Binance USDT-M perpetual futures** every 10 minutes and sends a Telegram alert when a coin is **oversold on both the 1h and 4h timeframes** (RSI(14) < 30 by default).

It does the same job as watching the [CoinGlass RSI Heatmap](https://www.coinglass.com/pro/i/RsiHeatMap) by hand. RSI is calculated directly from Binance kline data, so no paid API key is needed.

## Features

- Scans every USDT-M perpetual on Binance Futures (~500+ coins) in about 30 seconds
- Alerts only when **both** 1h and 4h RSI are below the threshold
- **No duplicate alerts:** a coin is reported once when it enters oversold. It is reported again only if it leaves oversold and later re-enters
- State is saved to disk, so restarting the bot does not resend old alerts
- Single-instance lock, so running it twice does not cause duplicate messages
- Runs silently in the background on Windows (`pythonw`), with logs written to `bot.log`
- No third-party dependencies (Python standard library only)
- Telegram messages in English or Turkish

## Example alert

```
🟢 1h + 4h RSI Oversold (< 30)
🕒 23.09.2026 20:09 · 528 coins scanned

RIVER  4h: 29.9  1h: 20.5  💲12.3450
```

## Setup

### 1. Requirements

- Python 3.9+
- Internet access to `fapi.binance.com` and `api.telegram.org`

### 2. Create a Telegram bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram and send `/newbot`.
2. Choose a name and a username (must end with `bot`).
3. Copy the **bot token** it gives you (format: `123456789:AA...`).

### 3. Configure

```bash
cp config.example.json config.json
```

Open `config.json` and paste your bot token into `telegram_bot_token`.

Then open your bot on Telegram, send `/start`, and run:

```bash
python rsi_bot.py --chat-id
```

Copy the printed `chat_id` into `telegram_chat_id`, then check that everything works:

```bash
python rsi_bot.py --test
```

> **Keep `config.json` private.** It contains your bot token and is listed in `.gitignore`.
> You can also provide the credentials through the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables instead.

### 4. Run

```bash
python rsi_bot.py          # run continuously (every 10 minutes)
python rsi_bot.py --once   # single scan, useful for testing
```

## Configuration

| Key | Default | Description |
|---|---|---|
| `telegram_bot_token` | `""` | Telegram bot token from BotFather |
| `telegram_chat_id` | `""` | Chat to send alerts to (user, group or channel) |
| `rsi_period` | `14` | RSI period |
| `oversold` | `30` | Alert when both 1h and 4h RSI are below this value |
| `interval_minutes` | `10` | Minutes between scans |
| `min_24h_volume_usdt` | `0` | Skip coins with lower 24h quote volume (e.g. `10000000` = $10M) |
| `language` | `"en"` | Telegram message language: `"en"` or `"tr"` |

## Running in the background on Windows

- **`start.bat`**: starts the bot in the background with no console window. Output goes to `bot.log`.
- **`stop.bat`**: stops the background bot.

**Start automatically when Windows starts:** press `Win + R`, type `shell:startup`, and put a shortcut to `start.bat` in that folder.

On Linux or macOS, run it with `nohup python3 rsi_bot.py &`, as a `systemd` service, or inside `tmux`/`screen`.

## How it works

1. Fetches the list of trading USDT-M perpetual contracts from `/fapi/v1/exchangeInfo`.
2. For each coin, downloads the last 99 **4h** candles and computes Wilder's RSI. The current, still-open candle is included, as on TradingView and CoinGlass.
3. Only if 4h RSI is below the threshold does it also fetch **1h** candles. This roughly halves the number of requests.
4. Coins oversold on both timeframes that were not already alerted are sent to Telegram in a single message.
5. `state.json` stores the coins that are currently oversold and already alerted. When a coin leaves oversold it is removed, so a later re-entry triggers a new alert.

Requests use `limit=99`, which costs weight 1 per request on Binance, and 8 parallel workers. This stays well within Binance's rate limits.

## Notes

- RSI values are computed from Binance Futures data and may differ slightly from other sources.
- Because the open candle is included, a coin hovering right at the threshold may produce repeated alerts.
- This tool is for informational purposes only and is **not financial advice**.
