"""PRODUCTION surface. Everything the live bot computes with -- and nothing else.

THE BOUNDARY

This file ships to the trading server. `research.py` does not.

The split is drawn along responsibility, not along topic:

    engine.py     what the LIVE bot computes with. Changes rarely. Deploys.
    research.py   what I compute with while testing ideas. Never deploys.

WHY IT WAS SPLIT

These lived in one file for months. Every hypothesis test edited a file that
was also sitting on the production server. The deployed copy and the local
copy drifted apart silently; the mirror check complained; and the drift got
resolved by pushing to the live server -- for no reason at all, because the
production functions had not changed even once.

The live bot imports exactly three things from here: `adx`, `atr`, `donchian`.
Nothing in production has ever called the simulator. Once that was noticed,
the boundary drew itself.

WHAT THIS BUYS

  * A hypothesis test can no longer touch production code by accident.
  * The deploy surface is small enough to audit by eye.
  * "Does the server run what I think it runs?" becomes checkable -- see
    `mirror_check.py`.

THE DEMO STRATEGY IS DELIBERATELY PLAIN

A textbook Donchian breakout with an ATR trailing stop. The point of this
repository is the split and the measurement discipline, not the signal.
"""
import csv
import math
import os
import statistics

HOUR_MS = 3600 * 1000
DAY_MS = 86400 * 1000

# Taker fee PER SIDE. A round turn costs twice this.
#
# This constant sat at 0.00055 for months -- a number taken from memory and
# never once reconciled against the exchange. The real rate on the account was
# 0.100%, nearly double. Understated costs make a backtest show an edge that
# will not exist in money; on one strategy the correction flipped the sign of
# the result outright.
#
# Reconcile this against the venue's fee endpoint, not against what you
# remember signing up for.
TAKER_FEE = 0.001

# One shared window start for EVERY instrument.
#
# Baskets downloaded at different times began on different dates, which handed
# whichever basket started earliest a couple of extra weeks of history -- and
# therefore an advantage in any cross-basket comparison. Trim on load, not in
# the CSVs: raw data stays intact, and the analysis window is one number that
# applies identically to everyone who goes through the engine.
START_MS = 0


# ------------------------------------------------------------------- loading

class Bar:
    __slots__ = ("ts", "o", "h", "l", "c", "v")

    def __init__(self, ts, o, h, l, c, v=0.0):
        self.ts, self.o, self.h, self.l, self.c, self.v = ts, o, h, l, c, v


def load_1h(path):
    """Aggregate 15m candles into hourly bars.

    Bars are keyed by the hour they OPEN. A partial trailing hour is dropped
    rather than emitted short: a bar built from two candles instead of four has
    a smaller range, and a smaller range silently shrinks ATR at exactly the
    moment the most recent bar matters most.
    """
    buckets = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = int(row["ts"])
            if ts < START_MS:
                continue
            buckets.setdefault(ts // HOUR_MS, []).append(row)

    bars = []
    for key in sorted(buckets):
        rows = buckets[key]
        if len(rows) < 4:
            continue
        rows.sort(key=lambda r: int(r["ts"]))
        bars.append(Bar(
            key * HOUR_MS,
            float(rows[0]["open"]),
            max(float(r["high"]) for r in rows),
            min(float(r["low"]) for r in rows),
            float(rows[-1]["close"]),
            sum(float(r.get("volume", 0) or 0) for r in rows),
        ))
    return bars


# ---------------------------------------------------------------- indicators

def atr(bars, n=14):
    """Wilder's ATR. None until enough bars have accumulated."""
    out = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        trs.append(b.h - b.l if i == 0 else
                   max(b.h - b.l,
                       abs(b.h - bars[i - 1].c),
                       abs(b.l - bars[i - 1].c)))
    run = None
    for i in range(len(bars)):
        if i + 1 < n:
            continue
        run = sum(trs[:n]) / n if run is None else (run * (n - 1) + trs[i]) / n
        out[i] = run
    return out


def adx(bars, n=14):
    """Classic ADX. None until enough bars have accumulated."""
    out = [None] * len(bars)
    if len(bars) < 2 * n + 2:
        return out
    tr, pdm, ndm = [0.0], [0.0], [0.0]
    for i in range(1, len(bars)):
        up = bars[i].h - bars[i - 1].h
        dn = bars[i - 1].l - bars[i].l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        ndm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(bars[i].h - bars[i].l,
                      abs(bars[i].h - bars[i - 1].c),
                      abs(bars[i].l - bars[i - 1].c)))
    str_ = sum(tr[1:n + 1])
    spdm = sum(pdm[1:n + 1])
    sndm = sum(ndm[1:n + 1])
    dxs = []
    for i in range(n + 1, len(bars)):
        str_ = str_ - str_ / n + tr[i]
        spdm = spdm - spdm / n + pdm[i]
        sndm = sndm - sndm / n + ndm[i]
        if str_ <= 0:
            continue
        pdi = 100 * spdm / str_
        ndi = 100 * sndm / str_
        s = pdi + ndi
        dx = 100 * abs(pdi - ndi) / s if s > 0 else 0.0
        dxs.append((i, dx))
        if len(dxs) == n:
            out[i] = sum(d for _, d in dxs) / n
        elif len(dxs) > n:
            out[i] = ((out[i - 1] * (n - 1) + dx) / n
                      if out[i - 1] is not None else None)
    return out


def donchian(bars, n):
    """Channel high/low over the n PRECEDING bars. The current bar is excluded.

    Excluding the current bar is not a detail. Include it and the channel top
    is, by construction, never below the current high -- so a breakout can
    never be detected, or worse, is detected using information from the bar
    being traded. That is lookahead, and it is the single easiest way to
    manufacture an edge that does not exist.
    """
    hi = [None] * len(bars)
    lo = [None] * len(bars)
    for i in range(n, len(bars)):
        window = bars[i - n:i]
        hi[i] = max(b.h for b in window)
        lo[i] = min(b.l for b in window)
    return hi, lo


# ------------------------------------------------------------------- results

class Trade:
    def __init__(self, sym, ts, side, entry, stop, exit_px, hours, reason=""):
        self.sym, self.ts, self.side = sym, ts, side
        self.entry, self.stop, self.exit = entry, stop, exit_px
        # Needed for chronological drawdown -- see metrics().
        self.exit_ts = ts + hours * HOUR_MS
        self.hours = hours
        self.reason = reason
        risk = abs(entry - stop)
        gross = (exit_px - entry) if side > 0 else (entry - exit_px)
        self.r_gross = gross / risk if risk > 0 else 0.0
        self.cost_r = (entry * 2 * TAKER_FEE) / risk if risk > 0 else 0.0
        self.r = self.r_gross - self.cost_r


def metrics(trades):
    """Summary statistics.

    DRAWDOWN IS COMPUTED IN EXIT-TIME ORDER, NOT LIST ORDER.

    This function used to trust the order of its argument, and the caller
    passed a per-symbol concatenation: every BTC trade, then every ETH trade,
    and so on. That is not a sequence anyone ever lived through, and it
    understated portfolio drawdown by nearly half -- 19.6R reported against
    33.9R actual on a 12-symbol portfolio.

    Risk-per-trade was being chosen off the understated number. A metric that
    is wrong in the safe-looking direction is worse than no metric.
    """
    if not trades:
        return dict(n=0)
    seq = sorted(trades, key=lambda t: getattr(t, "exit_ts", t.ts))
    rs = [t.r for t in seq]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    gross_profit, gross_loss = sum(wins), sum(losses)

    equity = peak = drawdown = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)

    sd = statistics.pstdev(rs) if len(rs) > 1 else 0.0
    return dict(
        n=len(rs),
        wr=100.0 * len(wins) / len(rs),
        pf=(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        avg_r=sum(rs) / len(rs),
        sum_r=sum(rs),
        dd_r=drawdown,
        t=(sum(rs) / len(rs) / sd * math.sqrt(len(rs))) if sd > 0 else 0.0,
    )


def fmt(name, m, extra=""):
    if not m.get("n"):
        return f"{name:<34} no trades"
    return (f"{name:<34} n={m['n']:<5} WR={m['wr']:5.1f}%  PF={m['pf']:5.2f}  "
            f"avgR={m['avg_r']:+6.3f}  sumR={m['sum_r']:+7.1f}  "
            f"DD={m['dd_r']:5.1f}  t={m['t']:+5.2f}{extra}")
