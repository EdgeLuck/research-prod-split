# research-prod-split

How to keep a trading backtest and a live trading bot in one codebase without
letting hypothesis testing edit production.

```
python research.py --demo     # runs on synthetic bars, no market data needed
python test_split.py          # 7/7
```

## The problem

A backtest engine and a live bot want the same indicators. So they share a
file. Then every hypothesis you test edits a file that is also sitting on the
production server.

What that looked like in practice:

- The deployed copy and the local copy drifted apart silently. Nothing alerts
  on mirror drift.
- The drift got resolved by pushing to the live trading server — while it held
  open positions — for no reason at all, because the production functions had
  not changed. Only the research code around them had.
- One review found the deployed engine was an older revision, missing two
  fixes that had been written, tested, and never actually shipped.
- Another found four cron wrappers that existed **only** on the server. No
  local copy, so they were in no backup and no code review, and nobody knew.

## The split

The boundary is drawn along **responsibility**, not along topic:

| | `engine.py` | `research.py` |
|---|---|---|
| what it is | what the live bot computes with | what I compute with while testing |
| changes | rarely | constantly |
| deploys | yes | **never** |
| contains | loading, indicators, result types, metrics | the simulator, hypothesis switches |

The rule that decides every case: **if a knob exists to answer "what if?", it
belongs in research. If the live bot reads it, it belongs in the engine.**

Finding the boundary was not a design exercise. The live bot turned out to
import exactly three things — `atr`, `adx`, `donchian` — and nothing in
production had ever called the simulator. The line was already there; it just
was not written down.

## What the tests actually guard

Not profitability — none of these care whether the strategy makes money.

**The boundary stays real.** `engine.py` must not import `research.py`. Without
this test the split is aspirational, and one convenient import undoes it.

**No duplicate indicators.** `research.py` must take its indicators from the
engine. Two implementations of ATR is how a backtest and a live bot come to
disagree about the same number while both look correct.

**The simulator is pinned.** A refactor that "should not change results" is a
claim. The simulator's output is hashed; changing it fails the suite until you
deliberately re-bless the hash, having looked at what moved. This is how the
original extraction was validated — pinned bit-for-bit across the full
instrument set before the split was accepted.

**No lookahead in the channel.** `donchian` must not see the bar it is
evaluated on. Include the current bar and breakouts become either undetectable
or self-fulfilling. It is the cheapest way there is to invent an edge.

**Costs are always charged.** Every trade carries a round-turn fee. See below
for why this has its own test.

**Drawdown is order-invariant.** Feed the trades in a deliberately wrong order;
the answer must not move.

## Two mistakes encoded in this repo

Both are in the code as comments, because both cost real money and both look
like details.

**Fees taken from memory.** The taker fee constant sat at 0.055% per side for
months — a number recalled rather than reconciled. The real rate was 0.100%,
nearly double. Understated costs make a backtest show an edge that will not
exist in money; on one strategy, correcting it flipped the sign of the result
outright. Reconcile against the venue's fee endpoint, not against what you
remember signing up for.

**Drawdown computed in list order.** `metrics()` used to trust the order of its
argument, and the caller passed a per-symbol concatenation: every BTC trade,
then every ETH trade. That is not a sequence anyone lived through. It
understated portfolio drawdown by nearly half — 19.6R reported against 33.9R
actual across 12 symbols — and risk per trade was being sized off the
understated number.

The demo prints both numbers so you can see them disagree. Note that on the
synthetic data the naive figure comes out *larger*: the direction of the error
depends on how losing streaks overlap in real time and is not predictable. The
lesson is not "naive is optimistic". It is that only one of the two numbers is
a fact about your account.

## mirror_check.py

Answers "does the server run what I think it runs?" by hashing every script on
both sides.

```bash
export MIRROR_HOST=user@host
export MIRROR_REMOTE_DIR=/opt/bot
python mirror_check.py
```

Three design notes:

- **It enumerates both sides.** An earlier version compared four files chosen
  from memory — which is the exact failure mode it was meant to catch,
  reproduced inside the tool. Files that exist only on the server are the
  dangerous direction and are reported loudly.
- **Line endings are not a difference.** A Windows workstation and a Linux
  server disagree about CRLF. Without normalization every file reports as
  changed, and you learn to ignore the output.
- **It is read-only.** It copies nothing and fixes nothing. Sometimes the
  server is ahead because of an emergency patch, and auto-syncing would
  destroy the fix. Deciding which side is right is a human's job.

## The demo strategy is deliberately plain

A textbook Donchian breakout with an ATR trailing stop, run on generated
random-walk bars. It loses money, which is what a breakout system should do on
a random walk once costs are charged.

That is the intent. This repository is about the split and the measurement
discipline; a demo that produced an impressive equity curve on fake data would
be worse than no demo. The live strategy this pattern was extracted from is
not included.

## Layout

```
engine.py        production surface: loading, indicators, Trade, metrics
research.py      research surface: simulator, hypothesis switches
demo_data.py     synthetic bars so the repo runs standalone
mirror_check.py  deployed vs local, read-only
test_split.py    boundary, pinning, and correctness guards
```

Standard library only. No pandas, no numpy.

## Related

Four repositories extracted from one production trading system.

- **[dead-ends](https://github.com/pashufa1981-glitch/dead-ends)** — the research log this
  split exists to serve: roughly three dozen rejected hypotheses, and the
  protocol behind the tests here. Both mistakes encoded in this repository —
  fees taken from memory, drawdown computed in list order — are written up there
  at length.
- **[subtractive-agents](https://github.com/pashufa1981-glitch/subtractive-agents)** — the
  decision architecture that consumes what the engine computes: one measured
  gate can act, every other agent can only veto or shrink.
- **[position-guard](https://github.com/pashufa1981-glitch/position-guard)** — the
  watchdog on the deployed side, checking that every open position has a stop
  that would actually fill.

MIT licensed.
