"""Quarterly DISCOVERY over a broad NSE universe (not the hand-picked universe.csv).

Seeds from NSE index constituent lists, fetches each company's Screener page (same
open pages the scanner already uses, cached per ticker per day), scores it with the
scanner's own logic, and surfaces >MIN_MCAP names that look like earnings-surprise
(PEAD) / strong-grower (HSCL-like) / structural-pattern candidates.

Output is LOCAL ONLY — data/discovery.json + discovery-review.md. Nothing is pushed
or published. Review, then fold chosen names into universe.csv by hand.

Run:  python discover.py          (resumable; skips tickers already fetched today)
      python discover.py --fast   (only re-score from cache, no new fetches)
"""
import csv, io, json, sys, time, random
from datetime import date
from pathlib import Path

import requests

import fetch
from scan import scan_one, num_from

BASE = Path(__file__).parent
DATA = BASE / "data"
SEEDS = DATA / "seeds"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/csv,*/*"}

MIN_MCAP = 1000.0   # Cr — the user's floor
# Iteration 1 seed: Nifty 500 (reliable). Broaden to smallcap/microcap once the
# output is validated — those NSE URLs are currently flaky from this host.
SEED_INDICES = {
    "nifty500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
}


def load_seed():
    """symbol -> {name, industry}; dedup across indices."""
    SEEDS.mkdir(parents=True, exist_ok=True)
    universe = {}
    for key, url in SEED_INDICES.items():
        cache = SEEDS / f"{key}.csv"
        if not (cache.exists() and cache.stat().st_mtime > time.time() - 86400):
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                if r.ok and "Symbol" in r.text:
                    cache.write_text(r.text, encoding="utf-8")
            except Exception as e:
                print(f"seed {key} download failed: {e}", file=sys.stderr)
        if not cache.exists():
            continue
        for row in csv.DictReader(io.StringIO(cache.read_text(encoding="utf-8"))):
            sym = (row.get("Symbol") or "").strip()
            if sym:
                universe.setdefault(sym, {"name": (row.get("Company Name") or sym).strip(),
                                          "industry": (row.get("Industry") or "").strip()})
    return universe


def classify(r):
    """Return (tier, why) for a scored row, or None to drop. Tiers, best first:
       PEAD  = quarterly earnings surprise (the scanner's pead flag)
       growth= HSCL-like strong+quality grower (>=25% PAT, >=15% op & sales)
       pattern = trips a structural pattern P1/P3/P5"""
    mcap = num_from(r.get("mcap"))
    if mcap is None or mcap < MIN_MCAP:
        return None
    q = f"{r['latest_q']}: PAT {r['q_pat_yoy']}% / op {r['q_op_yoy']}% / sales {r['q_sales_yoy']}%"
    if r["pead"]:
        return ("PEAD", q)
    # quarter-based tiers only count the target results season (scan.TARGET_QUARTER)
    if (r["reported_target"]
            and r["q_pat_yoy"] is not None and r["q_pat_yoy"] >= 25
            and r["q_op_yoy"] is not None and r["q_op_yoy"] >= 15
            and r["q_sales_yoy"] is not None and r["q_sales_yoy"] >= 15):
        return ("growth", q)
    pats = []
    if r["p1_mix_shift"]:
        pats.append("P1 mix")
    if r["p3_capacity"]:
        pats.append(f"P3 {r['p3_capacity']}")
    if r["p5_delever"]:
        pats.append("P5 delever")
    if pats:
        return ("pattern", ", ".join(pats))
    return None


def main(fast=False):
    DATA.mkdir(exist_ok=True)
    seed = load_seed()
    syms = sorted(seed)
    print(f"seed universe: {len(syms)} symbols across {len(SEED_INDICES)} indices")
    today = str(date.today())
    hits, scored, failed = [], 0, 0
    for i, sym in enumerate(syms):
        cache = DATA / f"{sym}.json"
        d = None
        if cache.exists():
            d = json.loads(cache.read_text(encoding="utf-8"))
            stale = d.get("fetched") != today
        else:
            stale = True
        if stale and not fast:
            d = fetch.fetch_company(sym)
            if d is not None:
                cache.write_text(json.dumps(d), encoding="utf-8")
                time.sleep(2 + random.random())   # be polite to screener.in
        if d is None:
            failed += 1
        else:
            try:
                r = scan_one(d, "", "")
                scored += 1
                tier = classify(r)
                if tier:
                    r["_tier"], r["_why"] = tier
                    r["_name"] = seed[sym]["name"]
                    r["_industry"] = seed[sym]["industry"]
                    hits.append(r)
            except Exception:
                failed += 1
        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{len(syms)}] scored={scored} hits={len(hits)} failed={failed}")

    order = {"PEAD": 0, "growth": 1, "pattern": 2}
    hits.sort(key=lambda r: (order[r["_tier"]], -(r["q_pat_yoy"] or -999)))
    out = {"discovered": today, "seed_size": len(syms), "scored": scored,
           "min_mcap_cr": MIN_MCAP, "hits": hits}
    (DATA / "discovery.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_review(out)
    print(f"\nDISCOVERY: {len(hits)} candidates (mcap>{MIN_MCAP:.0f}cr) | "
          f"PEAD {sum(h['_tier']=='PEAD' for h in hits)} · "
          f"growth {sum(h['_tier']=='growth' for h in hits)} · "
          f"pattern {sum(h['_tier']=='pattern' for h in hits)}")
    print(f"review: {BASE/'discovery-review.md'}")


def write_review(out):
    known = {row["ticker"].strip() for row in
             csv.DictReader(open(BASE / "universe.csv", encoding="utf-8-sig")) if row.get("ticker")}
    lines = [f"# Discovery — {out['discovered']}", "",
             f"Seed {out['seed_size']} NSE names (Nifty 500 + Smallcap 250 + Microcap 250), "
             f"scored {out['scored']}, market cap > ₹{out['min_mcap_cr']:.0f} Cr. "
             "LOCAL review only — not published. ★ = already in universe.csv.", ""]
    for tier, title in [("PEAD", "Tier 1 — PEAD earnings surprise"),
                        ("growth", "Tier 2 — Strong grower (HSCL-like)"),
                        ("pattern", "Tier 3 — Structural pattern (P1/P3/P5)")]:
        rows = [h for h in out["hits"] if h["_tier"] == tier]
        lines += [f"## {title} ({len(rows)})", "",
                  "| Stock | Ticker | MCap | PE | Signal | Industry |",
                  "|---|---|---|---|---|---|"]
        for h in rows:
            star = "★ " if h["ticker"] in known else ""
            lines.append(f"| {star}{h['_name']} | {h['ticker']} | {h.get('mcap','')} | "
                         f"{h.get('pe','')} | {h['_why']} | {h['_industry']} |")
        lines.append("")
    (BASE / "discovery-review.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main(fast="--fast" in sys.argv)
