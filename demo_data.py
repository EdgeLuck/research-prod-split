"""Synthetic bars, so the repository runs without shipping market data.

These are a geometric random walk with volatility clustering. They are NOT a
market simulator and results on them mean nothing about any strategy -- that
is deliberate. A demo that produced an impressive-looking equity curve on fake
data would be worse than no demo.

What the demo does prove is that the pipeline runs end to end: loading,
indicators, simulation, metrics.
"""
import math
import random

HOUR_MS = 3600 * 1000


class Bar:
    __slots__ = ("ts", "o", "h", "l", "c", "v")

    def __init__(self, ts, o, h, l, c, v=0.0):
        self.ts, self.o, self.h, self.l, self.c, self.v = ts, o, h, l, c, v


def synthetic_bars(n=4000, seed=1, start_price=100.0):
    rng = random.Random(seed)
    bars = []
    price = start_price
    vol = 0.004

    for i in range(n):
        # Volatility clusters: today's volatility remembers yesterday's.
        vol = max(0.001, min(0.02, vol * 0.97 + abs(rng.gauss(0, 0.0012))))

        o = price
        ret = rng.gauss(0, vol)
        c = max(0.01, o * math.exp(ret))
        wick = abs(rng.gauss(0, vol)) * o
        h = max(o, c) + wick * rng.random()
        l = max(0.005, min(o, c) - wick * rng.random())

        bars.append(Bar(i * HOUR_MS, o, h, l, c, rng.random() * 1000))
        price = c

    return bars
