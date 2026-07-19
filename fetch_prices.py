"""Fetch 5y of weekly OHLCV for every stock in universe.csv via Yahoo Finance.

Caches to data/prices/<TICKER>.json (one fetch per day). NSE symbol defaults to
"<ticker>.NS"; add a `yf_ticker` column to universe.csv to override.
"""
import csv, json, sys
from datetime import date
from pathlib import Path

import yfinance as yf

BASE = Path(__file__).parent
PRICES = BASE / "data" / "prices"


def fetch_one(symbol):
    df = yf.download(symbol, period="5y", interval="1wk", progress=False, auto_adjust=True)
    if df.empty:
        return None
    if hasattr(df.columns, "levels"):  # flatten MultiIndex columns
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "open": [round(float(v), 2) for v in df["Open"]],
        "high": [round(float(v), 2) for v in df["High"]],
        "low": [round(float(v), 2) for v in df["Low"]],
        "close": [round(float(v), 2) for v in df["Close"]],
        "volume": [int(v) for v in df["Volume"]],
    }


def main(force=False):
    PRICES.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(BASE / "universe.csv", encoding="utf-8-sig")))
    today = str(date.today())
    for i, row in enumerate(rows):
        t = row["ticker"].strip()
        if not t:
            continue
        symbol = (row.get("yf_ticker") or "").strip() or f"{t}.NS"
        out = PRICES / f"{t}.json"
        if out.exists() and not force:
            cached = json.loads(out.read_text(encoding="utf-8"))
            if cached.get("fetched") == today:
                print(f"[{i+1}/{len(rows)}] {t}: cached")
                continue
        data = fetch_one(symbol)
        if data is None:
            print(f"[{i+1}/{len(rows)}] {t}: FAILED ({symbol})", file=sys.stderr)
            continue
        data.update({"ticker": t, "symbol": symbol, "fetched": today})
        out.write_text(json.dumps(data), encoding="utf-8")
        print(f"[{i+1}/{len(rows)}] {t}: ok ({len(data['dates'])} weeks)")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
