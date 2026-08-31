"""Guards on the boundary between the production and research surfaces.

Three things are checked, and none of them are about whether the strategy
makes money.

1. PRODUCTION IMPORTS NOTHING FROM RESEARCH.
   The whole split is worth nothing if `engine.py` starts importing the
   simulator. This is the test that keeps the boundary real rather than
   aspirational.

2. THE SIMULATOR IS DETERMINISTIC AND PINNED.
   A refactor that "should not change results" is a claim. When the simulator
   was extracted from the production file, its output was pinned bit-for-bit
   against the pre-split implementation before the change was accepted. The
   golden hash below continues that: change the simulator and this fails until
   you deliberately re-bless it, having looked at what moved.

3. NO LOOKAHEAD IN THE CHANNEL.
   `donchian` must not see the bar it is evaluated on. Include the current bar
   and breakouts become undetectable or self-fulfilling -- the cheapest way
   there is to invent an edge.

    python test_split.py
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine
import research
from demo_data import synthetic_bars

# Re-bless deliberately, never reflexively: if this changes, the simulator's
# behaviour changed, and you should be able to say why before updating it.
GOLDEN = "71745033c7f4a13f"


def test_production_does_not_import_research():
    source = (Path(__file__).resolve().parent / "engine.py").read_text(encoding="utf-8")
    offenders = [line.strip() for line in source.splitlines()
                 if line.strip().startswith(("import research", "from research"))]
    ok = not offenders
    print(f"  [{'ok  ' if ok else 'FAIL'}] engine.py does not import research.py")
    if offenders:
        print(f"         found: {offenders}")
    return 0 if ok else 1


def test_research_uses_production_indicators():
    """The research side must not grow its own copy of an indicator.

    Two implementations of ATR is how a backtest and a live bot end up
    disagreeing about the same number without anyone noticing.
    """
    source = (Path(__file__).resolve().parent / "research.py").read_text(encoding="utf-8")
    ok = "from engine import" in source
    print(f"  [{'ok  ' if ok else 'FAIL'}] research.py takes indicators from engine.py")
    return 0 if ok else 1


def test_simulator_is_pinned():
    digest = hashlib.sha256()
    for seed in (1, 2, 3):
        bars = synthetic_bars(n=2000, seed=seed)
        for t in research.run(bars, sym=f"S{seed}"):
            digest.update(f"{t.ts}|{t.side}|{t.entry:.8f}|{t.stop:.8f}|"
                          f"{t.exit:.8f}|{t.hours}|{t.r:.8f}".encode())
    got = digest.hexdigest()[:16]
    ok = got == GOLDEN
    print(f"  [{'ok  ' if ok else 'FAIL'}] simulator output matches the golden hash")
    if not ok:
        print(f"         expected {GOLDEN}, got {got}")
        print("         if this change was intended, update GOLDEN -- after "
              "looking at what moved")
    return 0 if ok else 1


def test_donchian_excludes_current_bar():
    bars = synthetic_bars(n=200, seed=7)
    hi, lo = engine.donchian(bars, 20)
    failures = 0
    for i in range(20, len(bars)):
        window_hi = max(b.h for b in bars[i - 20:i])
        window_lo = min(b.l for b in bars[i - 20:i])
        if hi[i] != window_hi or lo[i] != window_lo:
            failures += 1
    ok = failures == 0
    print(f"  [{'ok  ' if ok else 'FAIL'}] donchian uses only preceding bars "
          f"(no lookahead)")
    return 0 if ok else 1


def test_breakout_is_detectable():
    """Sanity check on the above: if the channel included the current bar, a
    close could never exceed it, and this would find zero breakouts."""
    bars = synthetic_bars(n=3000, seed=11)
    trades = research.run(bars, sym="X")
    ok = len(trades) > 0
    print(f"  [{'ok  ' if ok else 'FAIL'}] breakouts are detectable "
          f"({len(trades)} trades)")
    return 0 if ok else 1


def test_costs_are_charged():
    """Every trade must carry a round-turn cost. A backtest with free trading
    is the single most common way to find an edge that is not there."""
    bars = synthetic_bars(n=3000, seed=13)
    trades = research.run(bars, sym="X")
    ok = bool(trades) and all(t.cost_r > 0 for t in trades)
    print(f"  [{'ok  ' if ok else 'FAIL'}] every trade is charged a round-turn cost")
    return 0 if ok else 1


def test_drawdown_uses_exit_order():
    """Feed trades in a deliberately wrong order; metrics must reorder them."""
    bars_a = synthetic_bars(n=2000, seed=3)
    bars_b = synthetic_bars(n=2000, seed=4)
    trades = research.run(bars_a, sym="A") + research.run(bars_b, sym="B")

    equity = peak = naive_dd = 0.0
    for t in trades:
        equity += t.r
        peak = max(peak, equity)
        naive_dd = max(naive_dd, peak - equity)

    proper_dd = engine.metrics(trades)["dd_r"]
    shuffled_dd = engine.metrics(list(reversed(trades)))["dd_r"]

    # Reordering the input must not change the answer -- that is the point.
    ok = abs(proper_dd - shuffled_dd) < 1e-9
    print(f"  [{'ok  ' if ok else 'FAIL'}] drawdown is invariant to input order "
          f"(exit-time {proper_dd:.2f}R vs list-order {naive_dd:.2f}R)")
    return 0 if ok else 1


def main():
    print("boundary:")
    failed = test_production_does_not_import_research()
    failed += test_research_uses_production_indicators()
    print("simulator:")
    failed += test_simulator_is_pinned()
    print("correctness:")
    failed += test_donchian_excludes_current_bar()
    failed += test_breakout_is_detectable()
    failed += test_costs_are_charged()
    failed += test_drawdown_uses_exit_order()

    total = 7
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
