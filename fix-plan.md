# Fix Plan — Outstanding Items Only

**Last full audit:** commits through `1560f48` (2026-06-27, "final items: N-LOG-1 and N-LIVE-LOCKOUT-1"). Today: 2026-06-27.
**Validation baseline:** `ruff check` ✅ · `ruff format --check` ✅ (31 files) · `pytest` 79/79 ✅ (was 75; +4 from new `tests/test_paper_trader.py`) · `mypy` **55 errors** (regressed 44 → 55 across the 5-commit batch).

**Status:** ~95% of audit items resolved. **4 outstanding items** remain.

---

## STILL OPEN — High

### UX-N1. Cancel-orders loop still freezes UI on full-universe signals

**Where:** `src/paper_trader.py:170-186` (in `reconcile`).

The "fix" in `605fef8` added a comment claiming it filters to actionable tickers — but the loop body still calls `self.cancel_open_orders(symbol=ticker)` once per ticker, serially. On a coinflip universe that's ~480 sequential Alpaca API calls before the next reconciliation, freezing the UI thread for many seconds.

**Real fix:** either batch via Alpaca's bulk endpoint or short-circuit when there are no conflicting open orders:

```python
actionable = [t for t, info in signals.items() if info["signal"] in ("BUY", "SELL")]
if actionable:
    # Bulk cancel — single round-trip
    self._trade_client.cancel_orders_for_symbols(symbols=actionable)
```

**Severity:** HIGH — UX-correctness. Visible lag spikes on coin-flip days.

### UX-N3. `Live` in `trade.py` is not context-managed

**Where:** `trade.py:230-275` (main cycle).

`live = Live(build_layout(t0), screen=True, refresh_per_second=4)` then `live.update(...)` in a loop, with `live.stop()` only on the explicit `KeyboardInterrupt` branch. **Any unhandled exception** (Alpaca outage, parse error, model load fail) leaves the terminal in alt-screen with the cursor invisible — the user must `reset` their TTY.

**Real fix:** wrap in a `with` block:

```python
from rich.live import Live

with Live(build_layout(t0), screen=True, refresh_per_second=4) as live:
    cycle = 0
    while True:
        try:
            ...
            live.update(build_layout(table))
            time.sleep(...)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Cycle error: {e}")
            time.sleep(30)
# `with` exits → terminal restored even on exceptions
```

**Severity:** HIGH — terminal-recovery correctness.

---

## STILL OPEN — Medium

### N-LOG-1 (partial). Trade audit log writer is TUI-only

**Where:** `textual_trader.py:385` defines `self._trade_log_path = "data/paper_trades.csvl"`; write happens at lines ~572-578 of the same file.

Running `uv run python trade.py --headless`, or any future caller of `PaperTrader` (notebook, eval, scripts), never appends to `paper_trades.csvl` → audit trail gap. The trail exists only when the TUI is the active UI.

**Real fix:** move the writer into `PaperTrader` so all callers share it:

```python
# src/paper_trader.py
def execute_orders(self, signals, positions):
    ...
    for result in results:
        if "FAIL" in str(result[2]):
            continue
        # ts, ticker, side, qty, price, score, cycle_id
        self._audit_trade(result)
```

…then remove the TUI's local write path.

**Severity:** MEDIUM — observability/compliance gap. Only matters if `trade.py` headless or other PaperTrader callers exist.

### mypy: 44 → 55 (newly introduced in 606fef8..1560f48)

**Where:** mostly `textual_trader.py` and `src/paper_trader.py`.

Three root causes identified by literal grep on the post-batch tree:

1. **`_timer` annotation widened to `object | None`** — `self._timer = None` in `__init__` (line ~381) without typing → mypy can't prove `.stop()` exists on the union. Fix: `self._timer: Timer | None = None`.
2. **`get_account()` / `get_positions()` return `Union[TradeAccount, dict, None]`** — the cache/fallback layer propagates `dict[str, Any]` into the rest of the codebase → cascade of `union-attr` errors when reading `.equity`, `.cash`, `.qty`, etc.
3. **`push_screen(LiquidateConfirm(...))` signature mismatch** — `ModalScreen` constructor takes a `ScreenResult`/`Callable` not generic args; callback typing mismatch.

**Real fix:**
- Annotate `_timer: Timer | None = None`
- Either narrow PaperTrader return types to `TradeAccount | Position` (drop fallback dicts now that the API lib is stable), or introduce `from_raw(raw: dict) -> TradeAccount` adapters
- Type the `ModalScreen` callback signature

**Severity:** MEDIUM — hygiene only, no runtime impact.

---

## RESOLVED (audit trail — won't be re-litigated)

| Item | Fix commit | Verification |
|---|---|---|
| N-MODEL-LEAK-1 (CRITICAL — selection-bias) | `dea2dae` | `eval_colab.py` uses `val.npz` for threshold opt, `test.npz` for selection-Sharpe. |
| H-NEW3 / M-NEW1 (display qty round→floor) | `402b5d1` | `math.floor(abs(pos['qty']))` at both display sites; `import math` both files. |
| UX-N2 (PAPER TRADING badge) | `402b5d1` | "PAPER TRADING" subtitle in TUI header (line ~392). |
| N-HIGH-1 (timer attr reordered) | `402b5d1` | `_timer = None` init + `if not None: stop()` guard. |
| L-NEW2 (sparkline reset on switch) | `402b5d1` | `self._equity_history.clear()` in `_switch_asset`. |
| N-LIVE-LOCKOUT-1 (live confirmation) | `1560f48` | `ALPACA_LIVE_CONFIRM` env-gate in `main.py:520-525` + `LiquidateConfirm` ModalScreen pattern. |
| M-NEW2 (model param pass-through) | `dad479e` | `run_inference(... model=None)` accepts optional pre-loaded model. |
| H-NEW4 (`set_interval` reschedule) | `dad479e` | `_timer` saved + `stop()`+`set_interval()` on +/- keys. |
| C-NEW1 (first-exception AttributeError) | `dad479e` | `_last_error = None`, `_error_count = 0` in `__init__`. |
| C-NEW2 (PaperTrader rebuild on switch) | `dad479e` | `self._trader = PaperTrader(self._config)` in `_switch_asset`. |
| market_state → threshold opt | `ce1a881` | `training/threshold.py` accepts `market_state`; duplicate timer init removed. |
| Zombie fold saving | `dea2dae` | Walk-forward folds persist correctly. |
| 605fef8 batch (infra hardening) | `605fef8` | 9 files / +963/-335. |

---

## DEFERRED — Phase-4+ (features, NOT bugs)

These aren't part of the bug-fix plan; logged so they don't show up in future audits as if they were bugs.

- **F-NL1** Limit-order & stop-loss support (currently market orders only)
- **F-NL2** Dry-run mode (log trades without submitting)
- **F-NL3** Corporate-actions handling (splits, dividends, mergers)
- **F-NL4** Fetch-failure telemetry + retry heuristics
- **F-NL5** USD-based risk sizing (vs current fixed `trade_buy_qty`)
- **F-NL6** Multi-strategy / date-bounded wallet rotation
- **F-NL7** Strategy docstring + architectural notes
- **F-NL8** Alpaca ticker-format compatibility (BRK.B, BF.B)

---

## Recommended PR sequence (≈3-4 h total)

| PR | Items | Effort |
|---|---|---|
| **PR-OPS-1 — UX blockers** | UX-N1, UX-N3 | ~1 h |
| **PR-OPS-2 — Cleanup** | N-LOG-1 centralization + mypy 55 → 0 | ~2-3 h |
