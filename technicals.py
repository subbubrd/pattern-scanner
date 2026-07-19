"""Weekly technical analysis for entry/exit context (TechnoFunda style:
fundamentals pick the stock, weekly structure picks the moment).

EMA set mirrors the pattern deck's charts: 20/50/100/200-week EMAs.

Status ladder (evaluated top-down, weekly closes):
  breakdown   : close < 200WEMA                       -> no trend; avoid / hard exit
  exit-signal : close < 50WEMA (but above 200)        -> trend-follower exit / trim zone
  extended    : uptrend and close > 1.30 x 50WEMA     -> chase risk; wait for pullback
  entry-zone  : uptrend and close within 10% of 20WEMA (either side) or between 50 & 20WEMA
  uptrend     : everything else in an uptrend         -> hold; add on pullback

Uptrend = close > 200WEMA, 50WEMA > 200WEMA, and 200WEMA rising vs 8 weeks ago.
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


def analyse(prices):
    """prices: dict from fetch_prices.py. Returns technical summary + chart series."""
    close = prices["close"]
    if len(close) < 30:
        return None
    e20, e50, e100, e200 = (ema(close, n) for n in (20, 50, 100, 200))
    c = close[-1]
    hi52 = max(prices["high"][-52:])
    lo52 = min(prices["low"][-52:])
    pct = lambda a, b: round((a - b) / b * 100, 1) if b else None

    uptrend = c > e200[-1] and e50[-1] > e200[-1] and e200[-1] > e200[-9]
    if c < e200[-1]:
        status = "breakdown"
    elif c < e50[-1]:
        status = "exit-signal"
    elif uptrend and c > 1.30 * e50[-1]:
        status = "extended"
    elif uptrend and (abs(pct(c, e20[-1])) <= 10 or e50[-1] <= c <= e20[-1]):
        status = "entry-zone"
    elif uptrend:
        status = "uptrend"
    else:
        status = "no-trend"

    n = 156  # 3 years of weekly bars for the dashboard chart
    return {
        "status": status,
        "breakout_watch": c >= 0.95 * hi52 and status not in ("breakdown", "exit-signal"),
        "close": c,
        "vs_e20": pct(c, e20[-1]), "vs_e50": pct(c, e50[-1]), "vs_e200": pct(c, e200[-1]),
        "from_52w_high": pct(c, hi52), "from_52w_low": pct(c, lo52),
        "rsi_w": rsi(close),
        "chart": {
            "dates": prices["dates"][-n:],
            "o": prices["open"][-n:], "h": prices["high"][-n:],
            "l": prices["low"][-n:], "c": close[-n:],
            "e20": [round(v, 1) for v in e20[-n:]],
            "e50": [round(v, 1) for v in e50[-n:]],
            "e100": [round(v, 1) for v in e100[-n:]],
            "e200": [round(v, 1) for v in e200[-n:]],
            "hi52": hi52,
        },
    }
