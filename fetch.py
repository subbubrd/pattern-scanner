"""Fetch fundamentals for the scanner universe from Screener.in.

Reads universe.csv, downloads each company's consolidated page (falls back to
standalone when consolidated is empty), parses the ratio header and the
quarterly / P&L / balance-sheet tables, and caches everything as
data/<TICKER>.json. Re-run any time; pages already fetched today are skipped
unless --force is passed.
"""
import csv, json, re, sys, time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
DATA = BASE / "data"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def parse_number(text):
    t = text.replace(",", "").replace("%", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_table(section):
    table = section.find("table")
    if table is None:
        return None
    head = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    rows = {}
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        label = re.sub(r"\s*\+$", "", cells[0])
        if not label:
            continue
        rows[label] = [parse_number(c) for c in cells[1:]]
    return {"columns": head[1:], "rows": rows}


def parse_ratios(soup):
    out = {}
    for li in soup.select("#top-ratios li"):
        name = li.find(class_="name")
        val = li.find(class_="value") or li.find(class_="number")
        if name and val:
            out[name.get_text(strip=True)] = val.get_text(" ", strip=True)
    return out


def fetch_company(ticker):
    for variant in ("consolidated/", ""):
        url = f"https://www.screener.in/company/{ticker}/{variant}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        quarters = soup.find("section", id="quarters")
        qt = parse_table(quarters) if quarters else None
        # consolidated pages of standalone-only companies come back empty
        if qt and qt["rows"].get("Sales") and any(v is not None for v in qt["rows"]["Sales"]):
            result = {"ticker": ticker, "url": url, "fetched": str(date.today()),
                      "ratios": parse_ratios(soup)}
            for sec_id, key in [("quarters", "quarterly"), ("profit-loss", "annual"),
                                ("balance-sheet", "balance_sheet"), ("cash-flow", "cash_flow")]:
                sec = soup.find("section", id=sec_id)
                result[key] = parse_table(sec) if sec else None
            return result
    return None


def main(force=False):
    DATA.mkdir(exist_ok=True)
    tickers = [row["ticker"].strip() for row in
               csv.DictReader(open(BASE / "universe.csv", encoding="utf-8-sig")) if row.get("ticker", "").strip()]
    today = str(date.today())
    for i, t in enumerate(tickers):
        out = DATA / f"{t}.json"
        if out.exists() and not force:
            cached = json.loads(out.read_text(encoding="utf-8"))
            if cached.get("fetched") == today:
                print(f"[{i+1}/{len(tickers)}] {t}: cached")
                continue
        data = fetch_company(t)
        if data is None:
            print(f"[{i+1}/{len(tickers)}] {t}: FAILED", file=sys.stderr)
            continue
        out.write_text(json.dumps(data, indent=1), encoding="utf-8")
        print(f"[{i+1}/{len(tickers)}] {t}: ok ({data['url'].rsplit('/company/')[-1]})")
        time.sleep(2.5)  # be polite to screener.in


if __name__ == "__main__":
    main(force="--force" in sys.argv)
