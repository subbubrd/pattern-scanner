"""Pattern scanner — scores each stock in the universe against the quantifiable
patterns from the TechnoFunda pattern library (see wiki/concepts/):

  P1 mix-shift   : OPM now vs 3y ago (>= +3pts) with quarterly margin trend intact
  P3 op-leverage : capacity-cycle phase from capex intensity + EBITDA-vs-sales growth gap
  P5 delever     : borrowings falling 2y running (>= 15%) with sales holding up
  PEAD           : latest-quarter YoY earnings surprise (PAT & operating profit both
                   explosive, top line growing) at a not-extreme PE and small/mid size;
                   "confirmed" when the weekly technical layer shows a breakout near 52w highs

P2 (proxy), P4 (regulatory), P6 (management) are qualitative — carried as tags
from universe.csv and shown on the dashboard.

A weekly technical layer (technicals.py, data via fetch_prices.py) adds an
entry/exit status per stock — breakdown / exit-signal / extended / entry-zone /
uptrend — plus the chart series embedded in the dashboard.

Outputs:
  data/scan_results.json
  dashboard.html               (self-contained, open in any browser)
  ../../wiki/analysis/scanner-latest.md
"""
import csv, json, re
from datetime import date
from pathlib import Path

from technicals import analyse

BASE = Path(__file__).parent
DATA = BASE / "data"
WIKI_ANALYSIS = BASE.parent.parent / "wiki" / "analysis"

MIN_OPM_DELTA = 3.0       # P1: percentage-point OPM improvement vs 3y ago
CAPEX_INTENSITY_MIN = 0.40  # P3: (CWIP + 2y fixed-asset adds) / gross block 2y ago
EBITDA_GROWTH_MULT = 1.4  # P3 inflection: EBITDA growing >= 1.4x sales
DELEVER_MIN_PCT = 15.0    # P5: minimum 2y borrowing reduction
# PEAD (latest single quarter, YoY vs the same quarter last year):
PEAD_PAT_YOY = 50.0       # net-profit surprise floor
PEAD_OP_YOY = 30.0        # operating-profit floor (beat isn't just other income / one-offs)
PEAD_SALES_YOY = 10.0     # top line must be growing too
PEAD_PE_MAX = 70.0        # "relatively low PE" — block only clearly rich valuations
PEAD_MCAP_MAX = 100000.0  # Cr — PEAD favours small/mid; soft ceiling
# PEAD only counts stocks that have actually REPORTED this quarter (freshness gate).
# Q1 FY27 = the Apr–Jun 2026 quarter, which Screener labels "Jun 2026".
# Bump each results season: Sep 2026 (Q2 FY27), Dec 2026 (Q3 FY27), Mar 2027 (Q4 FY27).
TARGET_QUARTER = "Jun 2026"


def num_from(s):
    """First number out of a Screener ratio string, e.g. '₹ 82,647 Cr.' -> 82647.0."""
    if not s:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", s.replace(",", ""))
    return float(m.group()) if m else None


def latest_quarter_label(cols, rows):
    """Column label of the most recent quarter that actually has reported data."""
    sales = (rows or {}).get("Sales") or []
    for i in range(len(sales) - 1, -1, -1):
        if sales[i] is not None:
            return cols[i] if i < len(cols) else None
    return None


def last(vals, n=1, offset=0):
    """n-th value from the end (offset 0 = latest), skipping trailing Nones."""
    clean = [v for v in vals if v is not None] if vals else []
    idx = len(clean) - 1 - offset
    return clean[idx] if 0 <= idx < len(clean) else None


def ttm(vals, offset=0):
    """Sum of 4 quarters ending `offset` quarters before the latest."""
    clean = [v for v in vals if v is not None] if vals else []
    seg = clean[len(clean) - 4 - offset: len(clean) - offset or None]
    return sum(seg) if len(seg) == 4 else None


def growth(now, before):
    if now is None or before in (None, 0):
        return None
    return round((now - before) / abs(before) * 100, 1)


def scan_one(d, tags, note):
    qtbl = d.get("quarterly") or {}
    q = qtbl.get("rows", {})
    latest_q = latest_quarter_label(qtbl.get("columns", []), q)
    a = d["annual"]["rows"] if d.get("annual") else {}
    b = d["balance_sheet"]["rows"] if d.get("balance_sheet") else {}

    sales_ttm, sales_prev = ttm(q.get("Sales")), ttm(q.get("Sales"), 4)
    ebitda_ttm, ebitda_prev = ttm(q.get("Operating Profit")), ttm(q.get("Operating Profit"), 4)
    sales_g, ebitda_g = growth(sales_ttm, sales_prev), growth(ebitda_ttm, ebitda_prev)
    pat_ttm, pat_prev = ttm(q.get("Net Profit")), ttm(q.get("Net Profit"), 4)
    pat_g = growth(pat_ttm, pat_prev)

    opm_now = round(ebitda_ttm / sales_ttm * 100, 1) if ebitda_ttm and sales_ttm else last(a.get("OPM %"))
    opm_3y = last(a.get("OPM %"), offset=3)
    opm_delta = round(opm_now - opm_3y, 1) if opm_now is not None and opm_3y is not None else None

    qopm = [v for v in (q.get("OPM %") or []) if v is not None]
    qtrend_up = len(qopm) >= 5 and qopm[-1] >= (sum(qopm[-5:-1]) / 4)

    # --- P1 mix shift ---
    p1 = bool(opm_delta is not None and opm_delta >= MIN_OPM_DELTA and qtrend_up)

    # --- P3 capacity cycle ---
    fa_now, fa_2y = last(b.get("Fixed Assets")), last(b.get("Fixed Assets"), offset=2)
    cwip = last(b.get("CWIP")) or 0
    intensity = None
    if fa_now is not None and fa_2y not in (None, 0):
        intensity = round((cwip + max(0.0, fa_now - fa_2y)) / fa_2y, 2)
    inflecting = (sales_g is not None and ebitda_g is not None and sales_g > 8
                  and ebitda_g > 0 and ebitda_g >= EBITDA_GROWTH_MULT * sales_g)
    building = (intensity is not None and intensity >= CAPEX_INTENSITY_MIN
                and opm_delta is not None and opm_delta <= 1.0)
    reverse = (sales_g is not None and ebitda_g is not None
               and sales_g < 0 and ebitda_g < sales_g)
    if inflecting:
        p3 = "inflecting"
    elif building:
        p3 = "building"          # phase 2-3: capex done/underway, margins not yet expanded
    else:
        p3 = None

    # --- P5 deleveraging ---
    b0, b1, b2 = last(b.get("Borrowings")), last(b.get("Borrowings"), offset=1), last(b.get("Borrowings"), offset=2)
    equity = (last(b.get("Equity Capital")) or 0) + (last(b.get("Reserves")) or 0)
    p5 = False
    delever_pct = None
    if None not in (b0, b1, b2) and b2 > 0:
        delever_pct = round((b2 - b0) / b2 * 100, 1)
        material = b2 >= 0.15 * equity if equity else b2 > 50
        sales_ok = sales_g is None or sales_g > -5
        p5 = b0 <= b1 <= b2 and delever_pct >= DELEVER_MIN_PCT and material and sales_ok

    # --- PEAD: latest single quarter vs the same quarter a year ago ---
    q_pat_yoy = growth(last(q.get("Net Profit")), last(q.get("Net Profit"), offset=4))
    q_sales_yoy = growth(last(q.get("Sales")), last(q.get("Sales"), offset=4))
    q_op_yoy = growth(last(q.get("Operating Profit")), last(q.get("Operating Profit"), offset=4))
    r = d.get("ratios", {})
    pe_v, mcap_v = num_from(r.get("Stock P/E")), num_from(r.get("Market Cap"))
    explosive = (q_pat_yoy is not None and q_pat_yoy >= PEAD_PAT_YOY
                 and q_op_yoy is not None and q_op_yoy >= PEAD_OP_YOY
                 and q_sales_yoy is not None and q_sales_yoy >= PEAD_SALES_YOY)
    pe_ok = pe_v is None or pe_v <= PEAD_PE_MAX      # None PE = turnaround; don't block
    size_ok = mcap_v is None or mcap_v <= PEAD_MCAP_MAX
    # freshness: only stocks that have actually reported TARGET_QUARTER (Q1 FY27) qualify.
    # Stocks yet to report still show their numbers, but never carry a PEAD flag.
    fresh = (latest_q == TARGET_QUARTER)
    pead = bool(explosive and pe_ok and size_ok and fresh)

    return {
        "ticker": d["ticker"], "name": "", "url": d["url"], "fetched": d["fetched"],
        "tags": tags, "note": note,
        "price": r.get("Current Price"), "mcap": r.get("Market Cap"),
        "pe": r.get("Stock P/E"), "roce": r.get("ROCE"),
        "sales_g": sales_g, "ebitda_g": ebitda_g, "pat_g": pat_g,
        "opm_now": opm_now, "opm_3y": opm_3y, "opm_delta": opm_delta,
        "capex_intensity": intensity, "delever_pct": delever_pct,
        "borrowings": [b2, b1, b0],
        "q_pat_yoy": q_pat_yoy, "q_sales_yoy": q_sales_yoy, "q_op_yoy": q_op_yoy,
        "latest_q": latest_q, "reported_target": fresh,
        "p1_mix_shift": p1, "p3_capacity": p3, "p5_delever": p5,
        "pead": pead, "pead_confirmed": False,   # confirmed set in main() once tech is attached
        "reverse_leverage": reverse,
    }


def main():
    rows = list(csv.DictReader(open(BASE / "universe.csv", encoding="utf-8-sig")))
    results = []
    for row in rows:
        t = row["ticker"].strip()
        f = DATA / f"{t}.json"
        if not t or not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        res = scan_one(d, row.get("tags", "").strip(), row.get("note", "").strip())
        res["name"] = row.get("name", t).strip()
        pf = DATA / "prices" / f"{t}.json"
        res["tech"] = analyse(json.loads(pf.read_text(encoding="utf-8"))) if pf.exists() else None
        # PEAD is "confirmed" when price is validating the surprise — breakout near 52w highs.
        res["pead_confirmed"] = bool(res["pead"] and res["tech"] and res["tech"]["breakout_watch"])
        results.append(res)

    # rank: number of quant signals (PEAD counts, +1 more if confirmed), then EBITDA growth
    def rank(r):
        score = sum([r["p1_mix_shift"], r["p3_capacity"] is not None, r["p5_delever"],
                     r["pead"], r["pead_confirmed"]])
        return (-score, -(r["ebitda_g"] or -999))
    results.sort(key=rank)

    out = {"scanned": str(date.today()), "target_quarter": TARGET_QUARTER, "results": results}
    (DATA / "scan_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    write_dashboard(out)
    write_wiki_page(out)
    n_hits = sum(1 for r in results if r["p1_mix_shift"] or r["p3_capacity"]
                 or r["p5_delever"] or r["pead"])
    print(f"scanned {len(results)} stocks, {n_hits} with at least one quant signal")
    print(f"dashboard: {BASE / 'dashboard.html'}")


def write_dashboard(out):
    template = (BASE / "dashboard_template.html").read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/null", json.dumps(out))
    (BASE / "dashboard.html").write_text(html, encoding="utf-8")
    # Artifact-ready variant: page content only (no doctype/html/head/body wrapper,
    # no local theme button — the artifact viewer supplies its own toggle).
    import re
    style = re.search(r"<style>.*?</style>", html, re.S).group(0)
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    body = re.sub(r'<button class="chip theme-toggle".*?</button>', "", body)
    (BASE / "dashboard_artifact.html").write_text(style + body, encoding="utf-8")
    # Public copy for GitHub Pages (https://subbubrd.github.io/pattern-scanner/),
    # served from docs/ and auto-updated by .github/workflows/scan.yml.
    docs = BASE / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(html, encoding="utf-8")


def write_wiki_page(out):
    if not WIKI_ANALYSIS.parent.exists():
        return  # running outside the vault (e.g. GitHub Actions) — no wiki to update
    WIKI_ANALYSIS.mkdir(exist_ok=True)
    lines = [
        "---", "type: analysis", f"created: 2026-07-19", f"updated: {out['scanned']}",
        "tags: [scanner, auto-generated]", "---", "",
        "# Pattern Scanner — Latest Run", "",
        f"Auto-generated by `tools/scanner/scan.py` on **{out['scanned']}** from Screener.in "
        "data. Signals implement [[pattern-recognition]] patterns P1/P3/P5; P2/P4/P6 are "
        "manual tags. Thresholds: OPM +3pts vs 3y (P1); capex intensity ≥ 0.40 & flat margins "
        "= building, EBITDA growth ≥ 1.4× sales growth = inflecting (P3); borrowings −15% "
        "over 2y (P5). Do not treat a signal as a recommendation — it is a queue for research.", "",
        f"PEAD ([[pead]]): **only stocks that have reported Q1 FY27 ({TARGET_QUARTER})** — "
        "YoY PAT ≥ 50% & op-profit ≥ 30% & sales ≥ 10%, PE ≤ 70, mcap ≤ ₹1,00,000 Cr. "
        "🚀 = technically confirmed (breakout near 52w highs). Refreshed daily.", "",
        "Technical status (weekly): breakdown < 200WEMA · exit-signal < 50WEMA · extended "
        "> 1.3× 50WEMA · entry-zone = uptrend near 20/50WEMA support · 🚀 = within 5% of 52w high.", "",
        "| Stock | PEAD (Q PAT YoY) | P1 mix | P3 capacity | P5 delever | Tech status | vs 50WEMA | 52w high | TTM EBITDA g | OPM Δ3y | Tags |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in out["results"]:
        wiki_link = f"[[{r['ticker']}]]"
        p1 = "✅" if r["p1_mix_shift"] else ""
        p3 = r["p3_capacity"] or ""
        p5 = "✅" if r["p5_delever"] else ""
        rev = " ⚠️rev" if r["reverse_leverage"] else ""
        if r["pead_confirmed"]:
            pead = f"🚀 +{r['q_pat_yoy']}%"
        elif r["pead"]:
            pead = f"✅ +{r['q_pat_yoy']}%"
        else:
            pead = ""
        t = r.get("tech") or {}
        tstat = (t.get("status") or "–") + (" 🚀" if t.get("breakout_watch") else "")
        lines.append(
            f"| {wiki_link} {r['name']} | {pead} | {p1} | {p3}{rev} | {p5} | {tstat} | "
            f"{t.get('vs_e50', '–')}% | {t.get('from_52w_high', '–')}% | "
            f"{r['ebitda_g'] if r['ebitda_g'] is not None else '–'}% | "
            f"{r['opm_delta'] if r['opm_delta'] is not None else '–'} | {r['tags']} |")
    lines += ["", f"Full interactive dashboard: `tools/scanner/dashboard.html` "
              f"(regenerate with `python tools/scanner/fetch.py && python tools/scanner/scan.py`)."]
    (WIKI_ANALYSIS / "scanner-latest.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
