"""Event-driven daily PEAD scan.

Instead of sweeping the whole market, ask NSE who actually announced results in
the last few days (corporate board-meetings feed, no login needed), then score
ONLY those companies against the PEAD screen in scan.py.

Typical load: a few dozen Screener fetches a day instead of 500 — light enough
to run daily.

Run:  python daily_results.py              (last 3 days)
      python daily_results.py --days 7
Output: data/daily_candidates.json + daily-candidates.md   (local review)
"""
import json, re, sys, time
from datetime import date, timedelta
from pathlib import Path

import requests

import fetch
from scan import scan_one, num_from, TARGET_QUARTER

BASE = Path(__file__).parent
DATA = BASE / "data"
MIN_MCAP = 1000.0
NSE = "https://www.nseindia.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def nse_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                      "Referer": f"{NSE}/companies-listing/corporate-filings-board-meetings"})
    s.get(NSE + "/", timeout=20)
    s.get(NSE + "/companies-listing/corporate-filings-board-meetings", timeout=20)
    return s


def reporters(days=3):
    """{symbol: {'name':..., 'date':...}} for companies whose board met on
    financial results within the last `days` days."""
    s = nse_session()
    to_d = date.today()
    frm = to_d - timedelta(days=days)
    url = (f"{NSE}/api/corporate-board-meetings?index=equities"
           f"&from_date={frm.strftime('%d-%m-%Y')}&to_date={to_d.strftime('%d-%m-%Y')}")
    j = s.get(url, timeout=30).json()
    rows = j if isinstance(j, list) else j.get("data", [])
    out = {}
    for x in rows:
        blob = f"{x.get('bm_purpose','')} {x.get('bm_desc','')}".lower()
        if "financial result" not in blob:
            continue
        sym = (x.get("bm_symbol") or "").strip()
        if sym:
            out.setdefault(sym, {"name": (x.get("sm_name") or sym).strip(),
                                 "date": x.get("bm_date", ""),
                                 "industry": (x.get("sm_indusrty") or "").strip()})
    return out


def main(days=3):
    DATA.mkdir(exist_ok=True)
    try:
        rep = reporters(days)
    except Exception as e:
        print(f"NSE calendar unavailable ({type(e).__name__}: {e}) — aborting", file=sys.stderr)
        return
    print(f"NSE: {len(rep)} companies announced results in the last {days} days")
    today = str(date.today())
    hits, checked, failed = [], 0, 0
    for i, (sym, meta) in enumerate(sorted(rep.items())):
        cache = DATA / f"{sym}.json"
        d = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
        if d is None or d.get("fetched") != today:
            d = fetch.fetch_company(sym)
            if d is not None:
                cache.write_text(json.dumps(d), encoding="utf-8")
                time.sleep(2)
        if d is None:
            failed += 1
            continue
        try:
            r = scan_one(d, "", "")
        except Exception:
            failed += 1
            continue
        checked += 1
        mcap = num_from(r.get("mcap"))
        if mcap is None or mcap < MIN_MCAP:
            continue
        if not r["reported_target"]:      # Screener not updated yet, or older quarter
            continue
        tier = "PEAD" if r["pead"] else (
            "growth" if (r["q_pat_yoy"] and r["q_pat_yoy"] >= 25
                         and r["q_op_yoy"] and r["q_op_yoy"] >= 15
                         and r["q_sales_yoy"] and r["q_sales_yoy"] >= 15) else None)
        if tier:
            r["_tier"], r["_name"] = tier, meta["name"]
            r["_announced"], r["_industry"] = meta["date"], meta["industry"]
            hits.append(r)
        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{len(rep)}] checked={checked} hits={len(hits)}")

    hits.sort(key=lambda r: ({"PEAD": 0, "growth": 1}[r["_tier"]], -(r["q_pat_yoy"] or -999)))
    out = {"run": today, "window_days": days, "target_quarter": TARGET_QUARTER,
           "announced": len(rep), "checked": checked, "hits": hits}
    (DATA / "daily_candidates.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_md(out)
    print(f"\n{len(hits)} candidates (mcap>₹{MIN_MCAP:.0f}cr, reported {TARGET_QUARTER}) "
          f"| PEAD {sum(h['_tier']=='PEAD' for h in hits)} · "
          f"growth {sum(h['_tier']=='growth' for h in hits)} | failed {failed}")


def write_md(out):
    import csv
    known = {r["ticker"].strip() for r in
             csv.DictReader(open(BASE / "universe.csv", encoding="utf-8-sig")) if r.get("ticker")}
    L = [f"# Daily results scan — {out['run']}", "",
         f"NSE announced-results calendar, last {out['window_days']} days: **{out['announced']}** "
         f"companies; {out['checked']} scored. Filter: market cap > ₹{MIN_MCAP:.0f} Cr and "
         f"actually reported **{out['target_quarter']} (Q1 FY27)**. ★ = already tracked.", ""]
    for tier, title in [("PEAD", "PEAD — earnings surprise"), ("growth", "Strong grower")]:
        rows = [h for h in out["hits"] if h["_tier"] == tier]
        L += [f"## {title} ({len(rows)})", "",
              "| Stock | Ticker | Announced | MCap | PE | PAT YoY | Op YoY | Sales YoY | Industry |",
              "|---|---|---|---|---|---|---|---|---|"]
        for h in rows:
            s = "★ " if h["ticker"] in known else ""
            L.append(f"| {s}{h['_name'][:30]} | {h['ticker']} | {h['_announced']} | {h.get('mcap','')} | "
                     f"{h.get('pe','')} | {h['q_pat_yoy']}% | {h['q_op_yoy']}% | {h['q_sales_yoy']}% | "
                     f"{h['_industry'][:20]} |")
        L.append("")
    (BASE / "daily-candidates.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    n = 3
    if "--days" in sys.argv:
        n = int(sys.argv[sys.argv.index("--days") + 1])
    main(n)
