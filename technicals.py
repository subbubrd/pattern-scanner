"""Weekly technical analysis for entry/exit context (TechnoFunda style:
fundamentals pick the stock, weekly structure picks the moment).

Charts use the 20-week and 40-week EMAs (40W ~ the classic long-term trend line,
close to the 200-day MA), plus the 52-week high/low band and swing pivots.

Status ladder (evaluated top-down, weekly closes):
  breakdown   : close < 40WMA                      -> no trend; avoid / hard exit
  exit-signal : close < 20WMA (but above 40WMA)    -> trend-follower exit / trim zone
  extended    : uptrend and close > 1.25 x 20WMA   -> chase risk; wait for pullback
  entry-zone  : uptrend and close within 8% of 20WMA
  uptrend     : everything else in an uptrend       -> hold; add on pullback

Uptrend = close > 40WMA, 20WMA > 40WMA, and 40WMA rising vs 8 weeks ago.
Overlay flag: breakout-watch when close is within 5% of the 52-week high.
"""


def ema(vals, span):
    if not vals:
        return []
    k = 2 / (span + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(vals, period=14):
    if len(vals) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = vals[i] - vals[i - 1]
        gains, losses = gains + max(d, 0), losses + max(-d, 0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 1)


def swing_pivots(highs, lows, k=5):
    """Confirmed weekly swing pivots: a pivot high is the highest high in a ±k-week
    window (needs k bars after it to confirm); pivot low symmetric. Returns points
    indexed into the passed arrays: [{'i':.., 'price':.., 'type':'high'|'low'}]."""
    out, n = [], len(highs)
    for i in range(k, n - k):
        if highs[i] == max(highs[i - k:i + k + 1]) and highs[i] > highs[i - 1]:
            out.append({"i": i, "price": round(highs[i], 1), "type": "high"})
        elif lows[i] == min(lows[i - k:i + k + 1]) and lows[i] < lows[i - 1]:
            out.append({"i": i, "price": round(lows[i], 1), "type": "low"})
    return out


def analyse(prices):
    """prices: dict from fetch_prices.py. Returns technical summary + chart series."""
    close = prices["close"]
    if len(close) < 30:
        return None
    e20, e40 = ema(close, 20), ema(close, 40)
    c = close[-1]
    hi52 = max(prices["high"][-52:])
    lo52 = min(prices["low"][-52:])
    pct = lambda a, b: round((a - b) / b * 100, 1) if b else None

    uptrend = c > e40[-1] and e20[-1] > e40[-1] and e40[-1] > e40[-9]
    if c < e40[-1]:
        status = "breakdown"
    elif c < e20[-1]:
        status = "exit-signal"
    elif uptrend and c > 1.25 * e20[-1]:
        status = "extended"
    elif uptrend and abs(pct(c, e20[-1])) <= 8:
        status = "entry-zone"
    elif uptrend:
        status = "uptrend"
    else:
        status = "no-trend"

    n = 156  # 3 years of weekly bars for the dashboard chart
    piv = swing_pivots(prices["high"][-n:], prices["low"][-n:], k=5)
    recent_high = next((p["price"] for p in reversed(piv) if p["type"] == "high"), None)
    recent_low = next((p["price"] for p in reversed(piv) if p["type"] == "low"), None)
    return {
        "status": status,
        "breakout_watch": c >= 0.95 * hi52 and status not in ("breakdown", "exit-signal"),
        "close": c,
        "vs_e20": pct(c, e20[-1]), "vs_e40": pct(c, e40[-1]),
        "from_52w_high": pct(c, hi52), "from_52w_low": pct(c, lo52),
        "pivot_high": recent_high, "pivot_low": recent_low,
        "rsi_w": rsi(close),
        "chart": {
            "dates": prices["dates"][-n:],
            "o": prices["open"][-n:], "h": prices["high"][-n:],
            "l": prices["low"][-n:], "c": close[-n:],
            "e20": [round(v, 1) for v in e20[-n:]],
            "e40": [round(v, 1) for v in e40[-n:]],
            "hi52": hi52, "lo52": lo52,
            "pivots": piv,
        },
    }
