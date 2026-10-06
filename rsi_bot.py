"""
RSI Oversold Scanner -> Telegram

Scans all Binance USDT-M perpetual futures and sends a Telegram alert when a coin's
RSI(14) is oversold (default < 30) on BOTH the 1h and 4h timeframes. Runs every 10 minutes.

Usage:
    python rsi_bot.py            -> run continuously (every 10 minutes)
    python rsi_bot.py --once     -> run a single scan and exit
    python rsi_bot.py --chat-id  -> print your chat_id (send /start to your bot first)
    python rsi_bot.py --test     -> send a test message to Telegram
"""

import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = "https://fapi.binance.com"
CONFIG_PATH = Path(__file__).with_name("config.json")
EXAMPLE_CONFIG_PATH = Path(__file__).with_name("config.example.json")
STATE_PATH = Path(__file__).with_name("state.json")
LOG_PATH = Path(__file__).with_name("bot.log")
LOCK_PORT = 47291

DEFAULT_CONFIG = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "rsi_period": 14,
    "oversold": 30,
    "interval_minutes": 10,
    "min_24h_volume_usdt": 0,
    "language": "en",
}

# Telegram message texts
TEXTS = {
    "en": {
        "title": "1h + 4h RSI Oversold",
        "scanned": "coins scanned",
        "test": "✅ RSI bot connection works.",
    },
    "tr": {
        "title": "1h + 4h RSI Oversold",
        "scanned": "coin tarandı",
        "test": "✅ RSI bot bağlantısı çalışıyor.",
    },
}


def load_config():
    if not CONFIG_PATH.exists():
        template = EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8") if EXAMPLE_CONFIG_PATH.exists() \
            else json.dumps(DEFAULT_CONFIG, indent=2)
        CONFIG_PATH.write_text(template, encoding="utf-8")
        print(f"Created {CONFIG_PATH}\nFill in your Telegram bot token and chat_id, then run again.")
        sys.exit(1)
    cfg = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
    # Environment variables override the config file
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", cfg["telegram_bot_token"])
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", cfg["telegram_chat_id"])
    return cfg


def text(cfg, key):
    return TEXTS.get(cfg["language"], TEXTS["en"])[key]


def http_get(url, params=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rsi-bot"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):  # rate limited
                wait = int(e.headers.get("Retry-After", 30))
                print(f"Rate limited, waiting {wait}s...")
                time.sleep(wait)
            elif attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2)
    return None


def get_symbols(min_volume):
    info = http_get(f"{BASE}/fapi/v1/exchangeInfo")
    symbols = {
        s["symbol"]
        for s in info["symbols"]
        if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
    }
    if min_volume > 0:
        tickers = http_get(f"{BASE}/fapi/v1/ticker/24hr")
        vol = {t["symbol"]: float(t["quoteVolume"]) for t in tickers}
        symbols = {s for s in symbols if vol.get(s, 0) >= min_volume}
    return sorted(symbols)


def rsi(closes, period):
    """Wilder's RSI (same method as TradingView / CoinGlass). Includes the current open candle."""
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def symbol_rsi(symbol, interval, period):
    # limit=99 -> Binance request weight 1 (100+ costs weight 2)
    kl = http_get(f"{BASE}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": 99})
    if not kl:
        return None
    closes = [float(k[4]) for k in kl]
    return rsi(closes, period), closes[-1]


def check_symbol(symbol, cfg):
    """Returns ("hit", data) / ("no", None) / ("err", None)."""
    try:
        period, level = cfg["rsi_period"], cfg["oversold"]
        r4 = symbol_rsi(symbol, "4h", period)
        if not r4 or r4[0] is None:
            return symbol, "err", None
        if r4[0] >= level:
            return symbol, "no", None  # skip the 1h request if 4h is not oversold
        r1 = symbol_rsi(symbol, "1h", period)
        if not r1 or r1[0] is None:
            return symbol, "err", None
        if r1[0] >= level:
            return symbol, "no", None
        return symbol, "hit", {"symbol": symbol, "rsi_4h": r4[0], "rsi_1h": r1[0], "price": r1[1]}
    except Exception as e:
        print(f"{symbol} error: {e}")
        return symbol, "err", None


def scan(cfg):
    symbols = get_symbols(cfg["min_24h_volume_usdt"])
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda s: check_symbol(s, cfg), symbols))
    hits = [d for _, st, d in results if st == "hit"]
    hits.sort(key=lambda r: r["rsi_4h"] + r["rsi_1h"])
    errors = {sym for sym, st, _ in results if st == "err"}
    return hits, errors, len(symbols)


def load_state():
    try:
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_state(state):
    STATE_PATH.write_text(json.dumps(sorted(state), indent=2), encoding="utf-8")


def send_telegram(cfg, message):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": cfg["telegram_chat_id"], "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        # Do not print the exception URL: it contains the bot token
        print(f"Telegram send error: {type(e).__name__} {getattr(e, 'code', '')}")
        return False


def fmt_price(p):
    return f"{p:.2f}" if p >= 100 else f"{p:.4f}" if p >= 1 else f"{p:.8f}".rstrip("0")


def build_message(new_hits, cfg, total):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        f"🟢 <b>{text(cfg, 'title')} (&lt; {cfg['oversold']})</b>",
        f"🕒 {now} · {total} {text(cfg, 'scanned')}\n",
    ]
    for h in new_hits:
        coin = h["symbol"].removesuffix("USDT")
        lines.append(
            f"<b>{coin}</b>  4h: <code>{h['rsi_4h']:.1f}</code>  1h: <code>{h['rsi_1h']:.1f}</code>  "
            f"💲{fmt_price(h['price'])}"
        )
    return "\n".join(lines)


def run_once(cfg, notified):
    """notified: coins that are currently oversold and have already been alerted.
    A coin is removed once it leaves oversold, so it is alerted again if it re-enters."""
    t0 = time.time()
    hits, errors, total = scan(cfg)
    current = {h["symbol"] for h in hits}
    new_hits = [h for h in hits if h["symbol"] not in notified]
    exited = notified - current - errors  # coins that failed to load are not treated as exited

    print(f"[{datetime.now():%H:%M:%S}] {total} coins scanned, {len(hits)} oversold, "
          f"{len(new_hits)} new ({time.time() - t0:.0f}s)")
    for h in new_hits:
        print(f"   NEW  {h['symbol']:<14} 4h={h['rsi_4h']:.1f}  1h={h['rsi_1h']:.1f}")
    if exited:
        print(f"   Left oversold: {', '.join(sorted(exited))}")

    state = (notified - exited) | current
    if new_hits and not send_telegram(cfg, build_message(new_hits, cfg, total)):
        state -= {h["symbol"] for h in new_hits}  # retry on the next scan if sending failed
    save_state(state)
    return state


def get_chat_id(cfg):
    upd = http_get(f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/getUpdates")
    chats = {}
    for u in upd.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        c = msg.get("chat")
        if c:
            chats[c["id"]] = c.get("title") or c.get("username") or c.get("first_name")
    if not chats:
        print("No messages found. Send /start to your bot on Telegram, then run this again.")
    for cid, name in chats.items():
        print(f"chat_id: {cid}   ({name})")


def setup_output():
    """When running without a console (pythonw), write output to bot.log."""
    if sys.stdout is None:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.replace(LOG_PATH.with_suffix(".old.log"))
        sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def single_instance():
    """Allow only one running bot at a time (via a local port lock)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        print("Bot is already running, not starting a second instance.")
        sys.exit(0)
    return s


def main():
    setup_output()
    cfg = load_config()
    args = sys.argv[1:]
    if "--chat-id" in args:
        return get_chat_id(cfg)
    if "--test" in args:
        ok = send_telegram(cfg, text(cfg, "test"))
        return print("Sent." if ok else "Failed to send, check your token / chat_id.")
    if "--once" in args:
        run_once(cfg, load_state())
        return

    _lock = single_instance()  # noqa: F841 (must stay open while the bot runs)
    interval = cfg["interval_minutes"] * 60
    print(f"[{datetime.now():%d.%m.%Y %H:%M}] Bot started. Scanning every {cfg['interval_minutes']} minutes.")
    notified = load_state()
    while True:
        start = time.time()
        try:
            notified = run_once(cfg, notified)
        except Exception as e:
            print(f"Scan error: {e}")
        time.sleep(max(0, interval - (time.time() - start)))


if __name__ == "__main__":
    main()
