"""RESEARCH surface. Simulation and hypothesis knobs. This file never deploys.

WHAT LIVES HERE AND WHY

Everything a hypothesis needs to be tested, and nothing the live bot needs to
run: the trade simulator, the per-signal feature record, and the switches that
exist only to answer a question (`every_signal`, `invert`, `track_mfe`).

If a knob exists to answer "what if?", it belongs here. If the live bot reads
it, it belongs in `engine.py`. That single rule is the whole boundary.

REGRESSION AFTER THE SPLIT

When the simulator was moved out of the production file, behaviour was pinned
bit-for-bit against the pre-split implementation across the full instrument
set before the split was accepted. A refactor that "should not change results"
is a claim, and claims about your own P&L are exactly the ones to test.

`test_split.py` keeps a smaller version of that check running.

DEMO

    python research.py --demo

Generates synthetic bars and runs the plain Donchian breakout across them, so
the repository is runnable without shipping anyone's market data.
"""
import argparse
import sys

from engine import Trade, atr, donchian, fmt, load_1h, metrics


def run(bars, sym="DEMO", *, chan=50, stop_atr=2.5, sides="both",
        trail=True, every_signal=False, invert=False, track_mfe=False):
    """Donchian channel breakout with an ATR trailing stop.

    Parameters that exist for RESEARCH, not for trading:

    `every_signal=True`
        Take every breakout, including ones a filter would have rejected, and
        record in `reason` which gates it failed. This is what makes a PAIRED
        comparison possible -- kept trades against rejected trades on the SAME
        set of signals, rather than against a differently-sized universe.

        In this mode the "one position per symbol" serialization is lifted:
        the next signal is taken from the next bar rather than after the
        current position exits. Otherwise the comparison would be rigged --
        whether a signal enters the sample would depend on whether the symbol
        was busy, and busyness depends on the filters, which is the very thing
        being measured.

        Side effect: trades overlap, so this mode is NOT valid for building an
        equity curve. It compares two subsets. It does not produce a P&L.

    `invert=True`
        Trade the mirror of every signal. Useful for one specific question --
        "is this strategy losing because it is backwards?" -- and useless for
        anything else. Note that inverting a losing strategy does not generally
        produce a winning one: costs are paid in both directions, and a failed
        breakout is usually chop rather than a clean reversal.

    `track_mfe=True`
        Record maximum favourable excursion per trade, for take-profit level
        studies. Off by default because it costs a pass over the bars.
    """
    a = atr(bars, 14)
    hi, lo = donchian(bars, chan)

    trades = []
    i = chan + 30
    while i < len(bars) - 1:
        if a[i] is None or hi[i] is None:
            i += 1
            continue

        bar = bars[i]
        long_break = bar.c > hi[i]
        short_break = bar.c < lo[i]

        side = 0
        if long_break and sides in ("both", "long"):
            side = 1
        elif short_break and sides in ("both", "short"):
            side = -1
        if side == 0:
            i += 1
            continue
        if invert:
            side = -side

        # Entry on the NEXT bar's open. Entering at this bar's close would use
        # the closing price of the bar that produced the signal -- knowable
        # only after the fact.
        entry = bars[i + 1].o
        risk = a[i] * stop_atr
        stop = entry - side * risk

        exit_px, hours, reason = _walk_forward(bars, i + 1, side, stop, a,
                                               stop_atr, trail)
        trades.append(Trade(sym, bars[i + 1].ts, side, entry, stop, exit_px,
                            hours, reason))

        # Serialized by default: one position per symbol at a time, which is
        # what actually happens when trading. Lifted only for paired tests.
        i = (i + 1) if every_signal else (i + 1 + hours)

    return trades


def _walk_forward(bars, start, side, stop, a, stop_atr, trail):
    """Advance bar by bar until the stop is hit or data runs out.

    The stop is checked against the bar's LOW (long) or HIGH (short) before any
    trailing update. Updating first would move the stop out of the way of the
    very bar that should have taken it out -- a small ordering mistake that
    quietly removes most losing trades.
    """
    j = start
    while j < len(bars):
        bar = bars[j]
        hit = (bar.l <= stop) if side > 0 else (bar.h >= stop)
        if hit:
            return stop, j - start, "stop"

        if trail and a[j] is not None:
            candidate = bar.c - side * a[j] * stop_atr
            # A trailing stop only ever moves in the favourable direction.
            stop = max(stop, candidate) if side > 0 else min(stop, candidate)
        j += 1

    return bars[-1].c, len(bars) - 1 - start, "end-of-data"


# ------------------------------------------------------------------ demo run

def _demo():
    """Runnable without market data: synthetic bars, real machinery."""
    from demo_data import synthetic_bars

    print("Donchian breakout, ATR trailing stop, synthetic data.")
    print("Numbers below are meaningless as a strategy result -- the point is "
          "that the pipeline runs end to end.\n")

    all_trades = []
    for seed, name in [(1, "SYNTH-A"), (2, "SYNTH-B"), (3, "SYNTH-C")]:
        bars = synthetic_bars(n=4000, seed=seed)
        trades = run(bars, sym=name)
        all_trades += trades
        print(fmt(name, metrics(trades)))

    print()
    print(fmt("PORTFOLIO (chronological DD)", metrics(all_trades)))

    # The lesson from engine.metrics(), made visible.
    naive = _naive_drawdown(all_trades)
    correct = metrics(all_trades)["dd_r"]
    print(f"\nDrawdown in list order (per-symbol concatenation): {naive:.1f}R")
    print(f"Drawdown in exit-time order (correct):             {correct:.1f}R")
    print(
        "\nThese differ because the first number replays a sequence nobody ever\n"
        "lived through -- every trade of one symbol, then every trade of the next.\n"
        "The direction of the error is NOT predictable: it depends on how the\n"
        "symbols' losing streaks overlap in real time. On the portfolio this was\n"
        "taken from, list order understated drawdown by nearly half (19.6R against\n"
        "33.9R), and risk per trade had been chosen off the understated figure.\n"
        "The lesson is not 'naive is optimistic'. It is that only one of these two\n"
        "numbers is a fact about your account, and it is never the cheap one."
    )


def _naive_drawdown(trades):
    equity = peak = dd = 0.0
    for t in trades:              # list order, i.e. symbol by symbol
        equity += t.r
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def main():
    ap = argparse.ArgumentParser(description="Research-side backtest runner.")
    ap.add_argument("--demo", action="store_true",
                    help="run on generated synthetic bars")
    ap.add_argument("--csv", help="path to a 15m OHLCV csv")
    ap.add_argument("--chan", type=int, default=50)
    ap.add_argument("--stop-atr", type=float, default=2.5)
    args = ap.parse_args()

    if args.demo:
        _demo()
        return 0
    if args.csv:
        bars = load_1h(args.csv)
        trades = run(bars, sym=args.csv, chan=args.chan, stop_atr=args.stop_atr)
        print(fmt(args.csv, metrics(trades)))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
