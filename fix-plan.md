# Fix Plan — Unresolved Items Only

**Last full audit:** commits through `d4fd4d6` (`fix: critical & high items from fix-plan`) on 2026-06-26. Today: 2026-06-27.
**Validation baseline:** `ruff check` ✅ · `ruff format --check` ✅ (31 files) · `pytest` ✅ (75/75) · `mypy` shows 48 pre-existing type errors (not introduced by these commits).
**Methodology:** Read each candidate on disk + sent to thinker-with-files-gemini for legitimacy / severity / fix validation. Three new bugs surfaced from this audit; one previously-flagged candidate (C-NEW2 fix shape) was refined with a cleaner alternative.

> _**Audit takeaway:** d4fd4d6 fixed 3 of its 5 self-declared items. C-NEW1 and M-NEW1 still fire. Two fresh bugs (timer reschedule + model reload-per-cycle) were uncovered that warrant a half-day bug-fix PR alongside the carry-forward UX workstream._

---

## STILL OPEN — Critical

### C-NEW1. `_refresh_cycle` AttributeError on first error (still NOT fixed despite `d4fd4d6`)

**Where:** `textual_trader.py:__init__` (lines 267-279) + `_refresh_cycle` except arm (lines 410-425).

Verified via direct read: `__init__` initializes `_err_strikes = 0` and `_last_session_path`, but **never initializes `_last_error` or `_error_count`**. The error-dedup branch added in commit `8c94c00` and claimed-fixed in `d4fd4d6` still tries to read these on the FIRST exception:

```python
self._err_strikes = 0                                   # line 276
self._last_session_path = Path("data/last_session.json") # line 277
self._load_session()                                      # line 278
self._asset_class = config.asset_class                   # line 279
# ← self._last_error / self._error_count NOT initialized
```

In `_refresh_cycle` except arm (lines 415-426):

```python
err = str(e)
if err == self._last_error:        # AttributeError on first error
    self._error_count += 1
    ...
```

**Fix:** add the two lines the commit message claimed were added:

```python
self._last_error: str | None = None
self._error_count = 0
```

**Why it didn't land:** edit-conflict during the `d4fd4d6` merge. Re-applying the same lines fixes the bug.

**Severity:** **CRITICAL** — first transient error (network blip / Alpaca rate-limit) crashes the worker UI silently. Subsequent attempts bump `_err_strikes` but no notification fires.

---

### C-NEW2. PaperTrader.data_client type never updates on asset-class switch (NEW, carry-forward)

**Where:** `src/paper_trader.py:__init__` (lines 26-30) + `textual_trader.py:_switch_asset` (lines 325-348).

`PaperTrader.__init__` picks `data_client` ONE TIME based on `config.asset_class` at construction:

```python
if config.asset_class == "crypto":
    self.data_client = CryptoHistoricalDataClient()       # line 28
else:
    self.data_client = StockHistoricalDataClient(key, secret)  # line 30
```

In `textual_trader.py`, pressing `S` (toggle) calls `_switch_asset("crypto")` which mutates `self._config.asset_class` but does **NOT** rebuild `self._trader`. Next cycle (`PaperTrader.get_latest_quotes`):

```python
if self.config.asset_class == "crypto":            # True on updated config
    req = CryptoLatestQuoteRequest(symbol_or_symbols=symbols)
if self.config.asset_class == "crypto":            # True again
    quotes = self.data_client.get_crypto_latest_quote(req)
    # ← self.data_client is STILL StockHistoricalDataClient → AttributeError
```

Caught by `except Exception as e: logger.warning(...)` → `quotes = {}`. Every BUY for the crypto side falls through to `NO_ASK` and **never executes**, forever.

`trade.py` / `--mode trade` (CLI) are unaffected (single-asset session). Only the Textual app via `s` key or button click triggers this.

**Cleanest fix (thinker-recommended):** rebuild the trader inside `_switch_asset`:

```python
def _switch_asset(self, target: str) -> None:
    self._asset_class = target
    self._config.asset_class = target
    if target == "crypto":
        from config import CRYPTO_PAIR_MAP
        self._config.tickers = CRYPTO_PAIR_MAP[self._config.crypto_pairs]
        ...
    else:
        ...
    self._trader = PaperTrader(self._config)        # rebuild here
    self._refresh_buttons()
    self.notify(f"Switched to {target}", severity="information")
    self.run_worker(self._refresh_cycle(), name="switch", exclusive=True)
```

**Severity:** **CRITICAL** — silent zero-trade state for the entire crypto side after any toggle, with no log indicating the failure on the user side.

---

## STILL OPEN — High

### H-NEW4. `set_interval` is never rescheduled on `+/-` keys — interval hot-keys are no-ops (NEW)

**Where:** `textual_trader.py:on_mount` line ~304 + `action_interval_up/down` lines ~514-521.

`on_mount` calls `self.set_interval(self._interval, self._on_timer)` exactly **once**, with no saved reference:

```python
self.set_interval(self._interval, self._on_timer)   # line ~304 of on_mount
```

The `+/-` actions only mutate `self._interval` and persist via `_save_session`; they do **not** reschedule:

```python
def action_interval_up(self) -> None:
    self._interval = min(3600, self._interval + 60)
    self._save_session()
    self.notify(f"Interval: {self._interval // 60}m", severity="information")
```

Textual's `set_interval` returns a `Timer` that ticks at the originally provided rate. Mutating `self._interval` only updates the value shown in the status bar (`"Next: ~16m"`) — actual cycle rate stays at whatever was set in `on_mount` (default 15m from `args.interval * 60`).

**Symptom:** press `+` five times → status bar shows `20m`, but cycles still fire every 15m.

**Fix:** save the Timer reference and reschedule:

```python
def on_mount(self) -> None:
    ...
    self._timer = self.set_interval(self._interval, self._on_timer)

def action_interval_up(self) -> None:
    self._interval = min(3600, self._interval + 60)
    if hasattr(self, "_timer"):
        self._timer.stop()
    self._timer = self.set_interval(self._interval, self._on_timer)
    self._save_session()
    self.notify(...)

# symmetric for action_interval_down
```

**Severity:** **HIGH** — the interval hot-key feature appears functional but does nothing. User cannot tune cadence without restarting the app.

---

### H-NEW3. Display lies about sellable quantity — `round()` vs `floor()` abs(qty) (carry-forward, both sites still open)

**Where 1:** `trade.py:85` — `pos_str = f"{round(pos['qty'])}" if pos else "—"`
**Where 2:** `textual_trader.py:461` — `pos_str = str(round(pos["qty"])) if pos else "—"`

Direct grep of `d4fd4d6` body shows the `math.floor` switch was claimed-fixed but did not land (both sites still use `round`).

`PaperTrader.reconcile` uses `math.floor(abs(pos["qty"]))` for SELL (explicit comment: *floor (not round): for fractional shares, rounding UP could flip a short into a long*). For a fractional long (e.g., 3.9 BTC crypto position):

| Display (Rich / textual) | Bot trades (Alpaca via reconcile) |
|---|---|
| `round(3.9) = 4` | `floor(3.9) = 3` |

User reads "Position: 4 BTC → next cycle sells 3 → display still reads `4` until Alpaca resyncs."

**Fix:**

```python
import math
pos_str = str(math.floor(abs(pos["qty"]))) if pos else "—"
```

…at both `trade.py:85` and `textual_trader.py:461`. One-line change at each site.

**Severity:** **HIGH** — display/trade mismatch makes positions uninterpretable; users can't reason about what they'll actually sell.

---

### M-NEW1. Same as H-NEW3 for `textual_trader._update_table` (carry-forward, still NOT fixed)

**Where:** `textual_trader.py:461` `pos_str = str(round(pos["qty"]))`.

Documented separately because the textual Rich site and the standalone `trade.py` site need the same fix; treating them together risks missing one.

---

## STILL OPEN — Medium

### M-NEW2. `run_inference` reloads the model from disk every cycle (NEW)

**Where:** `src/inference.py` line `model = load_model(config)` (line 56).

Every call to `run_inference` does:

1. `load_scaler(...)` — JSON read (cached in OS page cache).
2. `load_model(config)` — `torch.load(model_save_path, weights_only=True, map_location=device)` + `nn.Module` construction. ~50ms on NVMe, several seconds on slow disk or cold cache.
3. Forward pass.

Called every cycle (15 min on default) from both `textual_trader._refresh_cycle` and `trade.py`'s loop. Hot waste; on slow disks this stalls the worker thread.

**Fix:** pass a pre-loaded model in:

```python
def run_inference(
    config: Config,
    buy_threshold: float = 0.5,
    sell_threshold: float = 0.5,
    model: torch.nn.Module | None = None,
) -> dict[str, dict]:
    ...
    if model is None:
        model = load_model(config)
    ...
```

Then in `textual_trader.py:__init__`, load once:

```python
self._model = load_model(config)
```

…and pass it in via `self._model`. Same for `trade.py`'s main loop.

**Edge case worth checking:** the cancel-on-switch path (`_switch_asset`) needs to reload — but the new `model = None`-path returns to disk load. Document this clearly.

**Severity:** **MEDIUM** — wasted I/O + thread stalls the cycle. Visible as a "frozen" status bar during inference on slower disks.

---

### M1 (carried). `textual_trader.py` globally monkey-patches `tqdm` at import time

**Where:** `textual_trader.py:13-44` `_tqdm_std.tqdm = _NoopTqdm`.

Any module that imports `textual_trader` (e.g. `python -c "import textual_trader"`, accidental test import) replaces the global `tqdm` class for the rest of the process. Downstream tqdm progress bars in unrelated tests become no-ops. Masked today because tests don't import `textual_trader`; latent trap.

**Severity:** MEDIUM.

---

### M2 (carried). `main.prepare_walk_forward_splits` writes `fold_i_test.npz` no consumer reads

**Where:** `main.py:118-124`.

```python
np.savez(
    f"{fold_dir}/fold_{i}_test.npz",
    features=features[te],
    targets=targets[te],
    market_state=market_state[te],
)
```

The training loop only loads `fold_{i}_train.npz` and `fold_{i}_val.npz`. Test files are written on every walk-forward build (~15% wasted disk). Also misleading: a future ad-hoc eval that naively aggregates all `*.npz` files in `data/features/` silently mixes test data into training metrics.

**Minimal fix:** delete the `test.npz` save, or move test data to `data/features/walk_forward_tests/fold_{i}.npz` and document the intended consumer.

**Severity:** MEDIUM.

---

## STILL OPEN — Low

### L-NEW2. `_switch_asset` does not reset `equity_history` or `_prev_equity`

**Where:** `textual_trader.py:_switch_asset` (lines 325-348).

When switching from stocks to crypto (or back), `equity_history` keeps its prior-asset-class values, polluting the `Sparkline` widget's first view. The chart joins a 95% value on the stocks side to a brand-new crypto-equity baseline.

**Severity:** LOW (ugly, not a crash).

---

### L-NEW3 (NEW). `_load_session` survives partial `last_session.json` — kind of

**Where:** `textual_trader.py:_load_session` (lines 502-513).

```python
d = json.loads(self._last_session_path.read_text())
self._interval = d.get("interval", self._interval)
self._buy_t = d.get("buy_t", self._buy_t)
self._sell_t = d.get("sell_t", self._sell_t)
```

If `last_session.json` was hand-edited to omit `buy_t`/`sell_t`, those fields stay at constructor CLI defaults while `interval` jumps to disk value. Inconsistent state. Recoverable on next launch from CLI args.

**Severity:** LOW. (Thinker flagged this as not-a-bug — Claude's call to keep as documentation only.)

---

### N3-ponytail (NEW). Meaningless `ponytail:` comment in `eval_colab.py`

**Where:** `eval_colab.py:30` `# ponytail: cfg.tickers = range(N) for n_stocks count, actual names unused at inference`.

`set_n_stocks` (called on the next line) replaces cfg.tickers with placeholder indices. The word `ponytail` is meaningless (likely autocorrect artifact for `polyfill`/`pony` or just a typo). Cleanup nit.

**Severity:** NIT.

---

## STILL OPEN — UX / UI (open subset)

Reference: research-backed recommendations added in prior audit at `/Users/yuzu/Documents/Projects/TradingBot/fix-plan.md` history. Status as of this audit:

### Bucket A — Drop-in Textual widgets (still OPEN)

| ID | Item | Status | Note |
|----|------|--------|------|
| **UX2** | `Digits` widget headline for Equity/Cash | OPEN | MetricCard still uses raw string |
| **UX4** | `ProgressBar` for `inference running` | OPEN | not composed |
| **UX5** | `TabbedContent` for `Stocks / Crypto / Tuning` | OPEN | uses two `Button` widgets today |
| **UX6** | `Collapsible` around threshold-tuning controls | OPEN | |
| **UX7** | `MarkdownViewer` for in-app "Strategy Notes" | OPEN | |

### Bucket B/C — Real-time feedback / safety (still OPEN)

| ID | Item | Status |
|----|------|--------|
| **UX9** | "Last refreshed Xm ago" in header | PARTIAL (`now_str` only in status bar) |
| **UX10** | Row-level position-size colorization (yellow → red as % of cap) | OPEN |
| **UX12** | Tooltips on metric cards + main buttons | OPEN (asset buttons only) |
| **UX14** | Persistent hotkey-hint footer (lazygit-style) | OPEN |
| **UX15** | Liquidate-all modal (L → Y/Enter) | OPEN |
| **UX16** | Threshold-bound arrow-key confirmation (>0.95 prompts) | OPEN |

### Bucket D — Architectural roadmap (untouched)

| ID | Item | Effort |
|----|------|--------|
| **UX18** | Alpaca `TradingStream.subscribe_trade_updates` WebSocket replaces 15-min REST poll | 2-3d |
| **UX19** | Live market data stream (`StockDataStream`) for active positions | 1-2d |
| **UX20** | Multi-screen app stack (Dashboard / Tuning / Logs / Backtest / Notes) | 1d |
| **UX21** | User-configurable DataTable columns (saved to `~/.tradingbot/layout.json`) | 1d |
| **UX22** | Strategy inspector (model output vs consensus) | 0.5d |

### Bucket E — Misc

| ID | Item | Status |
|----|------|--------|
| **UX24** | Right-align numeric columns in DataTable | OPEN |
| **UX27** | Fault-tolerant status bar (extend to header) | OPEN (partial) |

---

## Resolved by recent commits (reference only)

| ID | Title | Commit | Evidence |
|----|-------|--------|----------|
| **H-NEW1** | Equity flashes red on no-change cycles | `d4fd4d6` | `if prev and curr and curr != prev:` guard at line 439 |
| **H-NEW2** | RichLog dividers rotated real trades out | `d4fd4d6` | divider only inside `if trades:` at line 387 |
| **L-NEW1** | `_load_session` silently swallowed JSON | `d4fd4d6` | `logger.warning(...)` at line 511 |
| **M3** | `pretrain.py` `top_head` DDP wrap had no CUDA guard | `2ee6e83-ish` + `d87f3b5` | `src/utils.wrap_ddp()` raises `RuntimeError` |
| **L4** | `_NoopTqdm.write` referenced `sys` defined later | `08bb220` | `import sys` at top |
| **L5** | `cfg.tickers = list(range(N))` in `eval_colab` | `2ee6e83-ish` | `set_n_stocks()` helper in `config.py` |
| **N2** | `_data_hash` only checked mtime + size | `d87f3b5` | `zlib.crc32(p.read_bytes()[:4096])` |
| **N7** | `reconcile` BUY logic didn't comment no-pyramid | `d87f3b5` | `# no-pyramid: won't add to existing positions` |

UX items implemented:

| ID | Title | Commit |
|----|-------|--------|
| **UX1** | Replace manual `_sparkline()` with built-in `Sparkline` widget | `2ee6e83` |
| **UX3** | `RichLog` for trade audit trail | `2ee6e83` |
| **UX8** | Bloomberg-style flash on equity change | `d6e5bad` |
| **UX11** | ▲ / ▼ symbols alongside red/green for P&L | `2ee6e83` |
| **UX13** | De-duplicated error toasts | `8c94c00` |
| **UX17** | "Disconnected" dot indicator at 3 strikes | `d6e5bad` |
| **UX23** | `Ctrl+P` binding + `Cmd+P` in `HelpScreen` | `8c94c00` |
| **UX25** | Persist interval/buy_t/sell_t to `data/last_session.json` | `8c94c00` |
| **UX26** | Reset `equity_history` on asset-class switch | `d6e5bad` |
| **UX28** | Dashed divider line in RichLog feed | `890dafd` → refined in `d4fd4d6` |

### Items claimed-fixed by `d4fd4d6` but **did NOT land** (verification)

| ID | Status | Why |
|----|--------|-----|
| **C-NEW1** | STILL OPEN | `_last_error`/`_error_count` referenced at lines 416-422, never initialized in `__init__` |
| **M-NEW1** | STILL OPEN | `_update_table` line 461 still `str(round(pos["qty"]))` |

---

## False positives (kept for future audits)

| ID | Candidate | Verdict |
|----|-----------|---------|
| F1.1 | `math.floor` round-trip fractional shares | Confirmed correct |
| F1.2 | `compute_features_for_date` uses BTC/USD for `market_state` | Not leakage — same pattern as SPY |
| F1.3 | DDP scaler rank-local stats | Same npz on every rank; broadcast block removed as dead code |
| F1.4 | `textual_trader` worker coordination | Mitigated by `exclusive=True` |
| F1.5 | `cancel_open_orders` blanket-cancel | Signature now requires `symbol` |
| F1.6 | `set_timer` pile-up | Textual one-shot timer; doesn't accumulate |
| F1.7 | Disconnect-dot doesn't reset | `_refresh_buttons()` overwrites each cycle |
| F1.8 | `_prev_equity` not reset causes spurious flash | Alpaca equity is unified per API key |
| F1.9 | `Path("data/last_session.json")` relative path | Standard for single-project CLI tool |
| **F1.10** (NEW) | `train.npz` skipped when features cached | `has_features = Path(train.npz).exists()` is the gate — verified gate is correct |
| **F1.11** (NEW) | `_load_session` partial overrides cause inconsistency | Recoverable; CLI args override next launch |
| **F1.12** (NEW) | `evaluate_model` `end=""` looks like missing newline | Intentional single-line UI formatting |
| **F1.13** (NEW) | `Sparkline.data` assigned from inside worker | Workers run on UI event loop; safe |

---

## Recommended order (remaining open items)

### Bug-fix PR — correctness bar (~½ day)

1. **C-NEW2** (CRITICAL) — rebuild `PaperTrader` inside `_switch_asset`. One extra line.
2. **C-NEW1** (CRITICAL) — re-apply the missing `__init__` lines (`_last_error=None`, `_error_count=0`).
3. **H-NEW4** (HIGH) — store Timer reference in on_mount, `_timer.stop()` + `set_interval` on +/-.
4. **H-NEW3** + **M-NEW1** — unify on `math.floor(abs(pos["qty"]))` at BOTH `textual_trader.py:461` and `trade.py:85`. One-line each.
5. **M-NEW2** (MEDIUM) — `run_inference(..., model=None)` param + load once in `__init__`. ~10-line refactor.

### Cleanup PR (~½ day)

6. **L-NEW2** — reset `equity_history` and `_prev_equity` in `_switch_asset`.
7. **M2** — delete unused `fold_i_test.npz` save.
8. **M1** — scope tqdm monkey-patch to App lifecycle (move inside `on_mount`, restore in `on_unmount`).
9. **N3-ponytail** — delete meaningless comment in `eval_colab.py`.
10. **L-NEW3** (optional) — tighten `_load_session` validation; rejects partial JSON.

### UX PR — drop-in widgets + cadence fix verified (~½ day)

- **UX2 / UX4 / UX12** (drop-in widgets + tooltips)
- **UX9** (header "Last refreshed Xm ago")
- **UX10 / UX24** (row colors + right-align)
- **UX5 / UX6** — Tabs/Collapsible
- Note: the `H-NEW4` fix removes a footgun that would otherwise confuse UX5 (Tabs) tuning users who assume `+` works.

### Architectural workstream (separate PRs, untouched)

| PR | Items | Wall time |
|----|-------|-----------|
| **PR-UX-A** | UX7 — markdown screen | ~1 day |
| **PR-UX-B** | UX15, UX16 — confirmation modals | ~0.5 day |
| **PR-WS-1** | UX18, UX19 — WebSocket migration | ~1 week |
| **PR-WS-2** | UX20, UX21, UX22 — multi-screen + config + inspector | ~2 days |

---

## Reference URLs (preserved)

- Textual widget gallery: <https://textual.textualize.io/widget_gallery/>
- Textual workers/timers/reactivity: <https://textual.textualize.io/guide/workers/> · <https://textual.textualize.io/guide/reactivity/>
- Textual `set_interval` semantics: returns a `Timer` that ticks at the originally specified interval; no auto-reschedule on value mutation. Source: <https://textual.textualize.io/api/timer/>
- K9s keyboard conventions: <https://github.com/derailed/k9s>
- Lazygit menu patterns: <https://github.com/jesseduffield/lazygit>
- Alpaca WebSocket streaming: <https://docs.alpaca.markets/us/docs/websocket-streaming>
