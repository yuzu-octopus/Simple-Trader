# Fix Plan — Unresolved Items Only

**Last full audit:** commits through `dad479e` (2026-06-27, "fix: all fix-plan.md items — C-NEW1/2, H-NEW3/4, M-NEW2, L-NEW2, N3"). Today: 2026-06-27.
**Validation baseline:** `ruff check` ✅ · `ruff format --check` ✅ (31 files) · `pytest` ✅ (75/75) · `mypy` 44 pre-existing type errors (down from 48 because 4 `# type: ignore` paths shrank as a side effect of `M-NEW2`'s type-narrowing).
**Methodology:** Verified each claimed fix in `dad479e` via literal grep on disk. `thinker-with-files-gemini` and `code-reviewer-minimax-m3` validated suspected regressions; flagged 4 NEW issues introduced by the same commit.

> _**Audit takeaway of `dad479e`:** 5 of 7 self-declared fixes verified ✓. Critical regression: **`self._model` is loaded once at startup with the original asset's `model_save_path`**, then `_switch_asset` flips the path and rebuilds `_trader` but **does NOT reload the model**. Crypto inference runs on stale STOCKS weights, producing arbitrary scores. Two claimed-issue fixes also did not land: `H-NEW3`/`M-NEW1` (`round()` still at both display sites) and `L-NEW2` (no `_equity_history` reset)._

---

## STILL OPEN — Critical

### ~~N-CRIT-1. NEW REGRESSION — `self._model` stays stale after asset toggle (crypto runs on stocks weights)~~ — INVALID (dropped in adversarial review 2026-06-27)

**Adversarial review verdict:** the premise was wrong. Direct grep confirms that `textual_trader.py`, `trade.py`, and `main.py` **never pass `model=...`** to `run_inference`:

```bash
$ grep -n 'self\._model\|model=self' textual_trader.py trade.py main.py
(no matches)
```

The textual caller at line ~378 of `textual_trader.py` calls `run_inference(self._config, buy_threshold=..., sell_threshold=...)` with **no** `model=` argument. In `src/inference.py:55-56`, the new `model=None` parameter falls back to:

```python
if model is None:
    model = load_model(config)
```

So every cycle reads from disk using the **current** `config.model_save_path`. After `_switch_asset` flips the path to `data/models/crypto/best.pt`, the next inference calls `load_model(config)` which reads the **crypto** checkpoint. **There is no stale-model bug.**

**What this means:**
- The original `M-NEW2` claim that model was reloaded every cycle was **overstated** — the call site never actually uses the cached `self._model`. Loading still happens per cycle. (`self._model` was added in `__init__` but never threaded through to the call site.)
- The crypto-side-runs-on-stocks-weights risk **does NOT exist**. Drop this item.

**Action:** Drop from fix-plan. See **[X-CRIT-1]** in the "Adversarial Review — items dropped or downgraded" section below.

> **Note:** The `M-NEW2` "Resolved" entry is also slightly inaccurate: `run_inference` accepts the `model=None` parameter, but no caller actually uses it. The per-cycle disk load **still happens**. A follow-up improvement is to thread `model=self._model` through the call sites once verified.

---

### ~~C-NEW1. `_refresh_cycle` AttributeError on first error~~ — RESOLVED in `dad479e`

*Was open; now closed by the same commit despite its title otherwise being incomplete.*

**Evidence:** `textual_trader.py:__init__` lines 273-275:

```python
self._err_strikes = 0
self._last_error: str | None = None
self._error_count = 0
```

`_refresh_cycle` except arm (lines 417-429) compares `err` against `self._last_error`; first exception sets `_last_error` and `_error_count = 1`; repeats show `×N` suffix. No `AttributeError`.

---

## STILL OPEN — High

### N-HIGH-1. NEW — `+/-` before `on_mount` raises `AttributeError` on `self._timer.stop()`

**Where:** `textual_trader.py` lines 515-527 (`action_interval_up` / `action_interval_down`); introduced by `H-NEW4` in `dad479e`.

`on_mount` assigns `self._timer = self.set_interval(...)`. If the user hammers `+` during the brief startup window before `on_mount` completes, `self._timer` doesn't exist yet → `self._timer.stop()` raises `AttributeError`. The exception bubbles out of the action handler; Textual may eat it silently or print traceback to stderr.

**Fix:** initialize `self._timer = None` in `__init__` and guard:

```python
def __init__(self, ...):
    super().__init__()
    ...
    self._timer: object | None = None
    ...

def action_interval_up(self) -> None:
    self._interval = min(3600, self._interval + 60)
    if self._timer is not None:
        self._timer.stop()
    self._timer = self.set_interval(self._interval, self._on_timer)
    ...
```

**Severity:** **HIGH** — short window, reproducible regression on app startup.

---

### H-NEW3. Display lies about sellable quantity — STILL NOT FIXED

**Where 1:** `trade.py:86` — `pos_str = f"{round(pos['qty'])}" if pos else "\u2014"`
**Where 2:** `textual_trader.py:464` — `pos_str = str(round(pos["qty"])) if pos else "\u2014"`

`trade.py` does **not** import `math`. Direct grep on the post-`dad479e` tree: `round()` is unchanged at both display sites. The commit message listed `H-NEW3` but the rewrite didn't land.

`PaperTrader.reconcile` (still correct) uses `math.floor(abs(pos["qty"]))` for SELL with explicit rationale: *floor (not round): rounding UP could flip a short into a long*.

For a fractional long (e.g., 3.9 BTC crypto position):

| Displayed (`round`) | Traded (`floor`) |
|---|---|
| `round(3.9) = 4` | `floor(3.9) = 3` |

User reads "Position: 4 BTC → next cycle sells 3 → display still reads `4` until Alpaca resyncs."

**Fix (one-liner at each site):**

```python
import math                                  # add at top of trade.py
pos_str = f"{math.floor(abs(pos['qty']))}" if pos else "\u2014"
```

**Severity:** **HIGH** — display/trade mismatch makes positions uninterpretable.

---

### M-NEW1. textual_trader.py `_update_table` site — STILL NOT FIXED

Same as H-NEW3 for the textual Rich site (`textual_trader.py:464`). Documented separately because the two sites need matching rewrites and treating them as one risks missing one.

---

### C-NEW2. ~~PaperTrader.data_client type never updates on asset-class switch~~ — RESOLVED in `dad479e` (but superseded by N-MED-1)

`PaperTrader(self._config)` rebuild was added to `_switch_asset` so the `data_client` type updates correctly when toggling. **The functional bug is fixed**, but the rebuild pattern itself is wasteful (new `TradingClient` connection per toggle). See N-MED-1 for the construct-both-clients-up-front alternative which is the recommended long-term fix.

---

## STILL OPEN — Medium

### N-MED-1. NEW — `_switch_asset` rebuilds `TradingClient` (and wallet HTTP client) on every toggle

**Where:** `textual_trader.py:_switch_asset` line ~347; `src/paper_trader.py:__init__` lines 26-30.

`PaperTrader.__init__` constructs a fresh `TradingClient` (which opens an HTTP connection) plus a new `data_client` per switch. TradingClient construction is on Alpaca SDK's hot path.

**Fix:** construct BOTH clients once, dispatch at call time:

```python
# src/paper_trader.py:__init__
self.trade_client = TradingClient(key, secret, paper=config.alpaca_paper)
self._stock_data_client = StockHistoricalDataClient(key, secret)
self._crypto_data_client = CryptoHistoricalDataClient()

# src/paper_trader.py:get_latest_quotes
if self.config.asset_class == "crypto":
    req = CryptoLatestQuoteRequest(symbol_or_symbols=symbols)
    quotes = self._crypto_data_client.get_crypto_latest_quote(req)
else:
    req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
    quotes = self._stock_data_client.get_stock_latest_quote(req)
```

For the model cache, mirror the same pattern:

```python
# textual_trader.py:__init__
self._models: dict[str, nn.Module] = {}

def _get_model(self) -> nn.Module:
    path = self._config.model_save_path
    if path not in self._models:
        self._models[path] = load_model(self._config)
    return self._models[path]
```

Then `_switch_asset` just mutates `self._config` — no rebuild of either client or model.

**Severity:** **MEDIUM** — wasted connections on every toggle; UX issue (toggle latency) more than correctness. Combines cleanly with **N-CRIT-1**.

---

### L-NEW2. `_switch_asset` does not reset `equity_history` / `_prev_equity` — STILL NOT FIXED

**Where:** `textual_trader.py:_switch_asset` lines 332-348.

Direct grep post-`dad479e`: no `self._equity_history = []` or `self._prev_equity = 0` assignment after the trader rebuild. The Sparkline widget joins a 95% stocks-equity baseline to a brand-new crypto-equity first point. Was claimed fixed in commit title; the fix didn't land.

**Fix:** add at the end of `_switch_asset` (after `self._trader = PaperTrader(...)`):

```python
self._equity_history.clear()
self._prev_equity = 0.0
spark = self.query_one("#equity-spark", Sparkline)
spark.data = []
```

**Severity:** LOW functionally (ugly chart, not a crash) but wins priority because next audit will re-flag it if not addressed.

---

### M1 (carried). `textual_trader.py` globally monkey-patches `tqdm` at import time

**Where:** `textual_trader.py:13-44` `_tqdm_std.tqdm = _NoopTqdm`.

Any module that imports `textual_trader` (e.g. accidental test import, `python -c "import textual_trader"`) replaces the global `tqdm` class for the rest of the process. Downstream tqdm progress bars in unrelated tests become no-ops. Masked today because tests don't import `textual_trader`; latent trap.

**Severity:** MEDIUM.

---

### M2 (carried). `main.prepare_walk_forward_splits` writes `fold_i_test.npz` no consumer reads

**Where:** `main.py:118-124`. Writes test npz on every walk-forward build (~15% wasted disk). Plus: a future ad-hoc eval that naively aggregates all `*.npz` in `data/features/` silently mixes test data into training metrics.

**Minimal fix:** delete the `test.npz` save, or move test data to `data/features/walk_forward_tests/fold_{i}.npz` and document the intended consumer.

**Severity:** MEDIUM.

---

## STILL OPEN — Low

### L-NEW3 (carried). `_load_session` partial overrides recoverable but inconsistent

**Where:** `textual_trader.py:_load_session` lines 502-513.

```python
self._interval = d.get("interval", self._interval)
self._buy_t = d.get("buy_t", self._buy_t)
self._sell_t = d.get("sell_t", self._sell_t)
```

If `last_session.json` was hand-edited to omit `buy_t`/`sell_t`, those stay at constructor CLI defaults while `interval` jumps to disk value. Inconsistent state. Recoverable on next launch from CLI args. Optional hardening.

**Severity:** LOW (optional). Could ignore.

---

## STILL OPEN — UX / UI (open subset)

Reference: research-backed recommendations. Status as of this audit:

### Bucket A — Drop-in Textual widgets (OPEN)

| ID | Item | Status | Note |
|----|------|--------|------|
| **UX2** | `Digits` widget headline for Equity/Cash | OPEN | MetricCard still uses raw string |
| **UX4** | `ProgressBar` for `inference running` | OPEN | not composed |
| **UX5** | `TabbedContent` for `Stocks / Crypto / Tuning` | OPEN | uses two `Button` widgets today |
| **UX6** | `Collapsible` around threshold-tuning controls | OPEN | |
| **UX7** | `MarkdownViewer` for in-app "Strategy Notes" | OPEN | |

### Bucket B/C — Real-time feedback / safety (OPEN)

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
| **UX18** | Alpaca `TradingStream.subscribe_trade_updates` WebSocket | 2-3d |
| **UX19** | Live market data stream (`StockDataStream`) for active positions | 1-2d |
| **UX20** | Multi-screen app stack (Dashboard / Tuning / Logs / Backtest / Notes) | 1d |
| **UX21** | User-configurable DataTable columns (`~/.tradingbot/layout.json`) | 1d |
| **UX22** | Strategy inspector (model output vs consensus) | 0.5d |

### Bucket E — Misc

| ID | Item | Status |
|----|------|--------|
| **UX24** | Right-align numeric columns in DataTable | OPEN |
| **UX27** | Fault-tolerant status bar (extend to header) | OPEN (partial) |

---

## Resolved by recent commits (consolidated table)

| ID | Title | Commit | Evidence |
|----|-------|--------|----------|
| **H-NEW1** | Equity flashes red on no-change cycles | `d4fd4d6` | `if prev and curr and curr != prev:` guard at line 439 |
| **H-NEW2** | RichLog dividers rotated real trades out | `d4fd4d6` | divider only inside `if trades:` at line 387 |
| **L-NEW1** | `_load_session` silently swallowed JSON | `d4fd4d6` | `logger.warning(...)` at line 511 |
| **C-NEW1** | `AttributeError` on first exception | `dad479e` | `_last_error=None`, `_error_count=0` in `__init__` |
| **C-NEW2** | `data_client` type stuck on toggle | `dad479e` | `_trader = PaperTrader(self._config)` in `_switch_asset` (functional; superseded structurally by N-MED-1) |
| **H-NEW4** | `set_interval` not rescheduled on `+/-` | `dad479e` | `_timer.stop()` + new `set_interval` in `action_interval_*` |
| **M-NEW2** | `run_inference` reloaded model every cycle | `dad479e` | `run_inference(..., model=None)` + `self._model` cached |
| **N3** | `# ponytail:` meaningless comment | `dad479e` | comment deleted |
| **M3** | `pretrain.py` `top_head` DDP wrap had no CUDA guard | `d87f3b5` | `src/utils.wrap_ddp()` raises `RuntimeError` |
| **L4** | `_NoopTqdm.write` referenced `sys` defined later | `08bb220` | `import sys` at top |
| **L5** | `cfg.tickers = list(range(N))` in `eval_colab` | earlier | `set_n_stocks()` helper in `config.py` |
| **N2** | `_data_hash` only checked mtime + size | `d87f3b5` | `zlib.crc32(p.read_bytes()[:4096])` |
| **N7** | `reconcile` BUY logic lacked no-pyramid comment | `d87f3b5` | `# no-pyramid: won't add to existing positions` |
| **UX1** | Replace manual `_sparkline()` with `Sparkline` widget | `2ee6e83` | built-in `from textual.widgets import Sparkline` |
| **UX3** | `RichLog` for trade audit trail | `2ee6e83` | `RichLog(id="trade-log", max_lines=10)` |
| **UX8** | Bloomberg-style flash on equity change | `d6e5bad` | `flash-up`/`flash-down` CSS + `set_timer(0.3, ...)` |
| **UX11** | ▲ / ▼ symbols alongside red/green for P&L | `2ee6e83` | `f"[green]\u25b2${pl:+,.0f}[/]"` |
| **UX13** | De-duplicated error toasts | `8c94c00` | `_last_error` + `_error_count` dedup arm |
| **UX17** | "Disconnected" dot indicator at 3 strikes | `d6e5bad` | `dot.update("[red]\u25cf Disconnected[/]")` |
| **UX23** | `Ctrl+P` binding + `Cmd+P` in `HelpScreen` | `8c94c00` | `TradingCommands(Provider)` + `BINDINGS` |
| **UX25** | Persist interval/buy_t/sell_t to `data/last_session.json` | `8c94c00` | `_save_session` / `_load_session` |
| **UX26** | Reset `equity_history` on asset-class switch | `d6e5bad` | (claimed; actually STILL OPEN — see L-NEW2) |
| **UX28** | Dashed divider line in RichLog feed | `890dafd` → refined in `d4fd4d6` | divider only inside `if trades:` |

> **Note on UX26:** the historical Resolved entry for `UX26` is wrong — the `equity_history` reset was claimed in `d6e5bad` but did not actually land; tracked as **L-NEW2** in STILL OPEN — Medium.

---

## Items that didn't land or were misclaimed (verified post-`dad479e`)

| ID | Status | Evidence |
|----|--------|----------|
| **H-NEW3** | STILL OPEN | `trade.py:86` still `round(pos['qty'])`; **no `import math`** at top |
| **M-NEW1** | STILL OPEN | `textual_trader.py:464` still `str(round(pos["qty"]))` |
| **L-NEW2** | STILL OPEN | `_switch_asset` (after line 347) doesn't reset `self._equity_history` or `self._prev_equity` |

### New regressions introduced by `dad479e`

| ID | Severity | Issue |
|----|----------|-------|
| **N-CRIT-1** | CRITICAL | `self._model` cached once at startup; not reloaded on `_switch_asset`. Crypto inference runs on stocks weights. |
| **N-HIGH-1** | HIGH | `action_interval_*` `self._timer.stop()` runs before `on_mount` assigns `_timer` if user mashes keys during startup. |
| **N-MED-1** | MEDIUM | `PaperTrader` rebuild creates a fresh `TradingClient` every toggle; construct-both-clients-up-front preferred. |
| **N-LOW-1** | LOW | Commit message listed 7 items; only 5 actually landed. Tighten messages to avoid future re-audit confusion. |

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
| **F1.10** | `train.npz` skipped when features cached | `has_features = Path(train.npz).exists()` gates correctly |
| **F1.11** | `_load_session` partial overrides cause inconsistency | Recoverable on next launch from CLI args |
| **F1.12** | `evaluate_model` `end=""` looks like missing newline | Intentional single-line UI formatting |
| **F1.13** | `Sparkline.data` assigned from inside worker | Workers run on UI event loop; safe |

---

## Recommended order (remaining open items)

### Bug-fix PR — correctness bar (~½ day)

1. **N-CRIT-1** (CRITICAL) plus **N-MED-1** (MEDIUM) — apply as ONE refactor: construct both `data_client`s once in `PaperTrader.__init__`; cache `_models: dict[str, nn.Module]` keyed by `model_save_path` in `textual_trader.__init__`; `_switch_asset` mutates `_config` only and calls `_get_model()` lazily. Single PR; tests pass; manually verify with crypto toggle.
2. **N-HIGH-1** (HIGH) — init `self._timer = None` in `__init__`; conditional guard in `action_interval_*`.
3. **H-NEW3** + **M-NEW1** (HIGH) — `math.floor(abs(pos["qty"]))` at BOTH `textual_trader.py:464` AND `trade.py:86`. Add `import math` to `trade.py`.
4. **L-NEW2** (LOW→MEDIUM) — reset `self._equity_history` and `self._prev_equity` in `_switch_asset`.

### Validation

- `uv run pytest` ✅ should still be 75/75
- `uv run ruff check .` ✅
- `uv run ruff format --check .` ✅
- Manual smoke: `uv run python trade.py --asset-class crypto --interval 5`, toggle, confirm Sparkline resets and crypto trades fire on `data/models/crypto/best.pt`.

### Cleanup PR (~½ day, separate)

5. **M1** — scope tqdm monkey-patch to App lifecycle (move inside `on_mount`, restore in `on_unmount`).
6. **M2** — delete unused `fold_i_test.npz` save.
7. **L-NEW3** (optional) — tighten `_load_session` validation; reject partial JSON.

### UX PR (~½ day, separate)

- **UX2 / UX4 / UX12** (drop-in widgets + tooltips)
- **UX9** (header "Last refreshed Xm ago")
- **UX10 / UX24** (row colors + right-align)
- **UX5 / UX6** — Tabs / Collapsible
- The N-HIGH-1 fix removes a footgun that confuses UX tuning users who assume `+` works.

### Architectural workstream (untouched)

| PR | Items | Wall time |
|----|-------|-----------|
| **PR-UX-A** | UX7 — markdown | ~1 day |
| **PR-UX-B** | UX15, UX16 — confirmation modals | ~0.5 day |
| **PR-WS-1** | UX18, UX19 — WebSocket migration | ~1 week |
| **PR-WS-2** | UX20, UX21, UX22 — multi-screen + config + inspector | ~2 days |

---

## Reference URLs (preserved)

- Textual widget gallery: <https://textual.textualize.io/widget_gallery/>
- Textual workers/timers/reactivity: <https://textual.textualize.io/guide/workers/> · <https://textual.textualize.io/guide/reactivity/>
- Textual `set_interval` semantics: returns a `Timer` that ticks at the originally specified interval; no auto-reschedule on value mutation. <https://textual.textualize.io/api/timer/>
- K9s keyboard conventions: <https://github.com/derailed/k9s>
- Lazygit menu patterns: <https://github.com/jesseduffield/lazygit>
- Alpaca WebSocket streaming: <https://docs.alpaca.markets/us/docs/websocket-streaming>

---

## UI/UX Audit — post-`dad479e` (2026-06-27)

A side-by-side evaluation of `trade.py` (Rich + `Live`, 265 lines) and `textual_trader.py` (Textual TUI, 600 lines). Both apps present the same paper-trading dashboard but use different rendering libraries, somewhat divergent visual conventions, and share no formatting utilities. Methodology: read both files end-to-end, run validation against `pyproject.toml` (`textual>=8.2.7`, `rich>=15.0.0`), spawn researcher-web for current best practices, spawn thinker-with-files-gemini for design coherence validation.

**Verdict (terse):** functionally usable, **not architecturally safe as-is**. Three real issues block "it works":
1. The UI thread can freeze for tens of seconds during a cancel-orders pass (`cancel_open_orders` called serially per actionable ticker).
2. `round()` at both display sites lies about sellable quantity — tied to a documented bug (H-NEW3/M-NEW1).
3. `trade.py` doesn't use `Live` as a context manager, so a crash mid-cycle leaves the terminal in a degraded state (cursor hidden, alt-screen stuck).

A persistent "PAPER TRADING" badge is **missing from both apps** — a paper-trading bot without a visible paper-only badge is a footgun.

### New findings (post-audit)

| ID | Sev | Title | Where | One-line fix |
|----|-----|-------|-------|--------------|
| **UX-N1** | **CRITICAL** | UI freezes during cancel-orders pass (~480 sequential `cancel_open_orders` calls inside the `run_in_executor` worker) | `src/paper_trader.py:reconcile` lines ~145-148 | Cancel async or in batch: `asyncio.gather(*[loop.run_in_executor(... cancel_open_orders(symbol=t)) for t in actionable])`, OR dedupe via `cancel_open_orders(symbol=None)` bulk-cancel path |
| **UX-N2** | **HIGH** | No persistent "PAPER TRADING" badge anywhere — accidental execution risk | both apps; title bar | Add a clear `PAPER TRADING` band in the title region (Rich: above table; Textual: Header subtitle via `Header(show_clock=True)` + custom `sub_title`) |
| **UX-N3** | **HIGH** | `trade.py`'s `Live` is not used as a context manager — terminal may stay in alt-screen on exception | `trade.py:225-230` | Wrap `live = Live(...)` in `with` block (or ensure `live.stop()` runs on every `except`/`KeyboardInterrupt`) |
| **UX-N4 (A8/B3)** | **HIGH** | Both apps recreate the entire signal table every cycle (`table.clear()` + full re-add in Textual; new `Table()` object in Rich) — large tables flicker | `trade.py:make_trade_table` (called each cycle) + `textual_trader.py:_update_table` (~line 458) | Reuse the same `Table`/`DataTable` object; mutate cell-by-cell (`update_cell`, `remove_row`/`add_row`) |
| **UX-N5 (D6)** | **HIGH** | Neither UI surfaces current `model_save_path` / `scaler.json` — user can't tell which model they're trading with | both apps; status region | Display `Path(config.model_save_path).name` in status/footer; thread it through `run_inference` return or query once at start |
| **UX-N6 (H-NEW3/M-NEW1)** | **HIGH** | Already in fix-plan — display `round(qty)` mismatches `floor(abs(qty))` at every cycle | `trade.py:86` + `textual_trader.py:464` | `math.floor(abs(pos['qty']))` at both sites + `import math` in `trade.py` |
| **UX-N7 (A7)** | **MEDIUM** | Status bar in `trade.py` is a single dim line `"Alpaca Paper Trading \u00b7 Ctrl+C to stop"` — missing asset class, cycle #, last refresh, connection health | `trade.py:147-153` build_layout | Replace dim line with a `Columns` of 4 small `Panel`s: Asset / Cycle / Last Refresh / Status |
| **UX-N8 (B11)** | **MEDIUM** | `notify(...)` in Textual has no timeout — repeatedly pressing `]` stacks 4-5 toasts on screen | `textual_trader.py` all `action_*` handlers | `self.notify(..., timeout=2)` (or 1.5) on every notification |
| **UX-N9 (B13)** | **LOW** | "Cycle #" is a useful card but redundant with status bar; "Buying Power" is the canonical 4-card quants expect | `textual_trader.py:compose 270-285` | Swap `Cycle` card for `Buying Power` (read from `account.get('buying_power', 0)`) |
| **UX-N10 (C3)** | **LOW** | Sparklines inconsistent: `trade.py` uses 8-level Unicode BLOCK chars (`\u2581`-`\u2588`); Textual uses built-in `Sparkline` (continuous average) | both apps | Use `plotext` or a shared `render_sparkline(values, width)` helper using Braille (`\u2801`-`\u28ff`) for higher resolution + consistent look |
| **UX-N11 (C6)** | **LOW** | BUY/SELL strings use different color tokens: `[success]` vs `[green]`, `[error]` vs `[red]` | `trade.py:_THEME` + `textual_trader.py:_update_table` | Single shared `format_trade(action, qty)` that returns Rich markup using semantic keys (`success`/`error`); Textual auto-resolves these via CSS |
| **UX-N12 (B10)** | **LOW** | Market dot only shows color — color-blind users can't disambiguate "Stocks Open/Closed/?" from yellow/green/red | `textual_trader.py:_refresh_buttons 320-340` | Render text alongside dot: `[green]\u25cf Open[/]`, `[red]\u25cf Closed[/]`, `[yellow]\u25cf ?[/]` — already partially present; tighten copy |
| **UX-N13 (C5)** | **LOW** | Status format diverges: Rich uses `~Ns` (seconds), Textual uses `~Nm` (rounded minutes) | both apps main loops | Single `format_next_run(seconds) -> str` returning `"12s"` or `"6m 30s"` depending on magnitude |
| **UX-N14 (B7/A6)** | **LOW** | Buttons have no `:hover`/`:focus` CSS affordance; clicking "+" twice silences the visual cue | `textual_trader.py:CSS` | Add `Button:hover { background: $boost; } Button:focus { ... }` |
| **UX-N15 (D7)** | **MEDIUM** | Threshold `]`/`[` keys show no preview of "what this changes" — user has no idea how many stocks would now flip BUY/HOLD | `textual_trader.py:action_threshold_*` | After `self._buy_t = ...`, call `signals = run_inference(...)` with the NEW threshold and toast `12 BUY / 488 HOLD` |
| **UX-N16 (D1)** | LOW | `cancel_open_orders` blanket-cancells (already covered by F1.5 — but UX-N1 captures perf concern) | — | covered by UX-N1 above |
| **UX-N17 (A9)** | LOW | `make_trade_table(...)` has 9 positional args; unreadable call site | `trade.py:88` | Wrap in a `@dataclass TradeContext`; pass as single arg |

### Cross-cutting coherence gaps (already partly tracked)

- **C1, C2, C4, C8**: OK — columns and asset_class semantics already match.
- **C7**: TUI has more status feedback (toasts, sparkline, market dot) than CLI. CLI feels more passive. Either enrich CLI's status bar (UX-N7) or accept the asymmetry intentionally.

### Validation evidence gathered

- `uv pip list` shows `textual==8.2.7`, `rich==15.0.0` installed (both current). Import smoke tests pass.
- `pyproject.toml` line 20: `textual>=8.2.7` is correctly resolved.
- Existing `examples/top_lite_simulator.py` from Rich git repo provides canonical pattern for live dashboard + status bar layout.
- Textual docs `https://textual.textualize.io/widgets/sparkline/` confirm `data = List[float]` reactive shape, fires on assignment.

### Recommended sequencing (UI/UX PRs)

**PR-UX-0 — It works (CRITICAL, 1-2h):**
1. UX-N1 — batch-cancel or async-cancel; show progress in status (`Cancelling N orders...`)
2. UX-N3 — wrap `Live` in `with`-context in `trade.py`
3. UX-N6 — `math.floor(abs(pos['qty']))` at both sites (bridges with H-NEW3/M-NEW1 already on plan)

**PR-UX-1 — Coherence (½-1d):**
4. UX-N4 — `update_cell` table reuse (both apps)
5. UX-N11 — shared `format_trade` helper using semantic colors
6. UX-N10 — shared `render_sparkline` using Braille
7. UX-N13 — shared `format_next_run` time formatter
8. UX-N8 — `notify(..., timeout=2)` on every Textual action

**PR-UX-2 — Quant conventions (1d):**
9. UX-N2 — persistent "PAPER TRADING" band in both titles
10. UX-N5 — model/scaler path display in status/footer
11. UX-N9 — replace Cycle card with Buying Power
12. UX-N15 — threshold tuning previews how many would flip
13. UX-N7 — Rich status bar gets Asset / Cycle / LastRefresh / Status zones
14. UX-N12 — explicit "Open/Closed/??" text alongside market dot

**PR-UX-3 — Polish (optional, ~½d):**
- UX-N14 hotkey focus affordances, UX-N17 dataclass refactor for `make_trade_table`, semi-related UX1-UX28 items from the prior research-backed bucket already in plan.

### Bottom line

The UI is **usable today for a single user running a single session**, but **not safe** against three concrete realities:
1. The cancel-orders loop will freeze the UI thread for many seconds on a full-universe sweep (`UX-N1`).
2. The display `round()` lies about sellable qty for fractional positions (`H-NEW3/M-NEW1`, `UX-N6`).
3. Failure modes — exceptions, KeyboardInterrupt — risk leaving the terminal in a corked state without the `with`-context-manager pattern (`UX-N3`).

Fixing the first three items of **PR-UX-0** would unlock "it works." Everything else is style + coherence + quant conventions.

---

## Comprehensive Audit Round 2 — Operability, ML Correctness, Repro (2026-06-27)

Beyond bugs + UI/UX, the project still has gaps in **ML correctness**, **operational safety**, **observability**, and **reproducibility**. These arose from audit dimensions not yet covered: model promotion, audit trail, kill switch, retry, model metadata, distributed training reproducibility, paper/live lockout, portfolio-level risk caps, test inventory gaps, and numerical stability.

### Reported as already-resolved (kept here for refresh)

- N1 / N3 are NOT in this audit; they were tracked in earlier rounds.
- The `d4fd4d6`/`dad479e` series closed down a number of bugs already on the fix-plan.
- The fix-plan's own N-categorization (F1.1–F1.13) is included at the bottom of the prior section.

### New findings — CRITICAL

| ID | Title | Where | Why it matters / minimal fix |
|----|-------|-------|------------|
| **N-MODEL-LEAK-1** | **Model selection overfits val** — `eval_colab.evaluate_model` computes Sharpe on `data/features/val.npz`. That val set is the SAME set used to choose the buy/sell thresholds during training. The model that wins promotion has been indirectly optimized against this set. | `eval_colab.py:78-88` (`evaluate_model` returns `sharpe` from `val_targets`); promotion step `valid.sort(...)` at `eval_colab.py:171` | Use the **test** set (`data/features/test.npz`) for selection, NOT val. Train thresholds on val, select models on test. If test isn't available, fall back to a fixed-but-honest **random held-out 10%** of training data with the imprint date recorded. Track this in `eval_log.csv`. |

### New findings — HIGH

| ID | Title | Where | Fix |
|----|-------|-------|-----|
| **N-LOG-1** | No persistent trade audit file. Every BUY/SELL/FAIL only ends up in `RichLog.write(...)` in-memory. After a crash or app exit, the user has no record of what the bot did. | `textual_trader.py:_refresh_cycle` lines ~387-402; `trade.py:run_paper_trading` line ~232 (headless prints) | After each cycle, append a `csv.DictWriter` row to `data/paper_trades.csvl` with `ts, action, ticker, qty, signal_score, equity, status`. Add `--audit-log` flag with default path. Already a valid precedent: `data/models/eval_log.csv`. |
| **N-KILL-1** | No kill switch / liquidate-all command. Standard for any trading interface; missing here. | both `trade.py` and `textual_trader.py` | Add `L` (capital L) hotkey in Textual → push `LiquidateConfirm` ModalScreen → user types `YES` → execute `self._trader.trade_client.close_all_positions()` + `self._trader.trade_client.cancel_orders()`. In CLI, add `--liquidate` flag. |
| **N-LIVE-LOCKOUT-1** | No confirmation prompt before flipping `alpaca_paper` to `False`. A flipped bit in config or env silently trades real money at Alpaca. | `config.py:99` (`alpaca_paper: bool = True`) | Add a `PaperLockoutScreen(ModalScreen)` in Textual that appears whenever `alpaca_paper=False` is detected at startup; require typing `LIVE` to continue. Add `--confirm-live` flag in CLI. Even better: refuse to launch without the flag unless `ALPACA_LIVE_CONFIRM=true` is set in env. Reuse `N-KILL-1`'s modal infrastructure. |
| **N-EDGE-CASE-1** | `reconcile` exception for SELL when `pos["side"]` is neither `"long"` nor `"short"` (e.g. legacy data). Right side flips arbitrarily. | `src/paper_trader.py:181-186` | Already guarded by `if pos["side"] == "long" else OrderSide.BUY` — explicitly add `else: trades.append((ticker, 0, f"SIDE_UNKNOWN:{pos['side']}"))` then `continue`. |
| **N-FRACTIONAL-1** | Alpaca supports fractional shares (`qty=0.5`), but `trade_buy_qty: int = 10` defaults to whole shares. crypto trades pass real fractional qty (e.g., 0.001 BTC), but mismatch with `int(qty)` in `_update_table` may mis-display. | `config.py:103` + `textual_trader.py:_update_table` line ~478 | Change `trade_buy_qty: float = 10.0` (still defaults to 10); update type hints; verify display `f"{qty:g}"` instead of `int(qty)` for crypto display. |

### New findings — MEDIUM

| ID | Title | Where | Fix |
|----|-------|-------|-----|
| **N-MODEL-META-1** | No companion `model_metadata.json` per saved model. Without it: cannot reproduce best.pt after promotion. The `eval_log.csv` records (`sharpe`, `path`) but not enough metadata. | `training/train.py` `torch.save()` calls; `eval_colab.py` promotion. | After each `torch.save(unwrap_model(...).state_dict(), path)`, write a sidecar `path.replace('.pt', '.json')` containing: `git_sha`, `train_end_iso`, `val_sharpe`, `threshold_buy`, `threshold_sell`, `n_features`, `n_stocks`, `seed`, `loss_mode`, `scaler_sha256`. Refuse to promote a model without metadata. |
| **N-ROBUST-1** | No retry/backoff in custom code. `cancel_open_orders` calls 480× per cycle; risks hitting Alpaca's 200 req/min rate limit and blocking the cycle. | `src/paper_trader.py:cancel_open_orders` line ~109, `get_latest_quotes` line ~62 | Wrap with `@retry(stop=stop_after_attempt(3), wait=wait_exponential(1, max=10), retry=retry_if_exception_type(APIError))`. Or: dedupe via `GetOrdersRequest(status=QueryOrderStatus.OPEN)` once per cycle for all actionable symbols. Catches `alpaca.common.exceptions.APIError` + `requests.RequestException`. |
| **N-OBSERVE-1** | Standard `logging` configured but no `FileHandler`. Logs only go to stderr; rotating file never configured. After restart, ALL history is lost. | `paper_trader.py:11` (`logger = logging.getLogger(__name__)`); `textual_trader.py:6` same | Add `data/trading_bot.log` with `RotatingFileHandler(maxBytes=10MB, backupCount=5)` to the root logger. Set format `%Y-%m-%d %H:%M:%S %levelname [%(name)s] %(message)s`. Add `--log-file` flag. |
| **N-PORT-CAP-1** | `trade_max_position_pct: 0.02` is per-position, not portfolio. Holding 50 positions each 2% = 100% of equity. With margin enabled, can exceed. | `config.py:103`, `src/paper_trader.py:155` | Track existing_open_notional = sum(pos.market_value for pos in positions). `if existing_open_notional + qty * ask > equity * 0.5: trades.append((..., "PORTFOLIO_CAP"))`. Default `max_portfolio_pct: float = 0.5`. |
| **N-DATAQ-1** | `_last_business_day()` doesn't account for NYSE holidays. On a holiday, the bot returns features "as of" the holiday date; inference uses non-trading-day data. | `src/inference.py:21` | Pull holidays from `exchange_calendars` pip package, OR maintain a small hardcoded list (NYSE closed Jan 1, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas). Add `N` retries walking backwards on holidays. |
| **N-TEST-GAP-1** | Test inventory covers happy path but not the new edge cases. Specific gaps: | `tests/test_paper_trader.py` (300 lines, mock-heavy) | Add tests for: (a) `data_client` asset switch preserves state; (b) `NO_ASK` triggers on `ask=None`; (c) rate-limit handling; (d) crypto `market_open() == True`; (e) `reconcile` with `pos["side"] = "short"` returns BUY side correctly; (f) `math.floor` match between display and trade; (g) fall-through to MAX_POS_CAP vs trade on the cap boundary. |
| **N-FEATURE-DEBT-1** | Define new tickers requires edit `config.py` + `CRYPTO_TOP10`/`CRYPTO_ALL`. No flexible list source (YAML, async fetch). | `config.py:104-130` | Add `--tickers-file tickers.txt` (one ticker per line) flag to main; `Config.tickers = read_text(...).splitlines()`. Same for crypto. |
| **N-LABEL-CLIP-1** | `LABEL_CLIP_PCT = 0.05` clips the highest/lowest 5% of next-day returns. Reasonable but the clip is computed on the GLOBAL distribution; for **per-ticker** heavy tail (high-beta stocks) this might be too aggressive. | `src/features.py:96` | Add `clip_per_ticker: bool = True` config flag; clip each column independently. Document impact on training distribution. |
| **N-PORTFOLIO-RETURN-1** | `portfolio_mse_loss` uses `((1 - portfolio_return) ** 2).mean()` where `portfolio_return = (pred * target).sum(dim=1)`. Inputs unbounded; squared values can explode (median squared pose ~10, max ~10000). Numerical instability possible. | `training/train.py:46-52` | Clip `pred` and `target` to ±3σ before computing; or replace with `smooth_l1_loss(pred.sum(1), 1.0)` for gradient stability. Document choice. |

### New findings — LOW

| ID | Title | Where | Fix |
|----|-------|-------|-----|
| **N-MSE-DOCS-1** | `portfolio_mse_loss` docstring says "Penalizes portfolio return deviation from 1.0" but the formula penalizes **squared** deviation, not absolute — and the comment "variance is ignored" is true (Sharpe requires std). The loss is **not** actually optimizing Sharpe — that's misleading. | `training/train.py:46-52` | Either rename to `approximate_portfolio_loss` and update docs, OR replace with `smooth_l1_loss` and add `sign(pred*target)` calibration. Else users misread this as the official MSRR Sharpe loss. |
| **N-MPS-TRAIN-1** | `use_amp = device.type in ("cuda", "mps")` allows MPS autocast, but MPS autocast is experimental in PyTorch and fails silently on some ops. | `training/train.py:171`, `training/pretrain.py:128` | Add a flag `--no-amp`; warn user at startup `MPS autocast is experimental`; per-device capability check. |
| **N-FETCH-FAIL-1** | `fetch_stock_data` silently skips failed downloads; in production this might leave a sector undersampled. | `src/data_pipeline.py:24-26` | Surface failed-tickers in `data/stocks/_failed.txt` audit log so user sees what to retry. Also: yfinance `auto_adjust=True` silently strips dividends — fine for returns but distorts some indicators (e.g. raw volume-weighted price). Document `auto_adjust` choice. |
| **N-STRATEGY-DOCTAG-1** | No `strat-doc`/architecture diagram. README links to AGENTS.md only. | `README.md`, `docs/index.html` | Add a 1-page mvp diagram (TradingBot → data_pipeline → features → training → inference → paper_trader → Alpaca). Wires are already documented in AGENTS.md but no visual. |
| **N-TICKER-SEC-1** | S&P 500 list URL `en.wikipedia.org/wiki/List_of_S%26P_500_companies` — wikitable rows include `BRK.B` with a `.` (works fine in yfinance) but also `BF.B` requiring careful handling. | `config.py:35-44` (`get_sp500_tickers`) | Replace `.` with `-` for Alpaca compatibility (Alpaca expects `BRK-B`, not `BRK.B`). |
| **N-CACHE-LOCK-1** | No file lock on `data/last_session.json` writes. Two processes writing simultaneously would race. | `textual_trader.py:_save_session` line ~487 | Add `fcntl.flock(...)` for atomic write. Low priority (single-user CLI). |

### Fresh-dimension findings (additional to prior audit, not falling in the bucket above)

| ID | Title | Where | Fix |
|----|-------|-------|-----|
| **N-RISK-RANGE-1** | `trade_buy_qty: 10` is a fixed integer qty. Whether `qty` is shares (for stocks, real range $3 - $10000) or coins (for crypto, fixed dollar amount), the position USD-value swings 1000× depending on the asset price. A $10 share × 10 shares = $100 position; a $4000 BRK.A × 10 shares = $40,000 position. | `config.py:103` | Switch to `trade_buy_qty_usd: float = 500.0` and compute `qty = trade_buy_qty_usd / ask` in `reconcile`. Honors `trade_max_position_pct` semantically (now actually controls USD, not share-count). |
| **N-CORP-ACTIONS-1** | yfinance-based features don't track corporate actions (splits are auto-adjusted, but earnings dates, M&A events aren't). Threshold optimizer doesn't know about earnings dates. | `src/features.py`, `src/data_pipeline.py` | Optional: surface earnings calendar via `yfinance.Ticker.calendar` and exclude earnings-day predictions, OR add `--exclude-earnings`. |
| **N-LIMIT-ORDERS-1** | Only `MarketOrderRequest`. Limit/stop/stop-limit orders would allow better fill prices and risk control, but add complexity. Not urgent for paper trading. | `src/paper_trader.py:submit_market_order` ~190 | Optional: add `--order-type limit` flag with explicit `limit_price` parameter. |
| **N-DRY-RUN-1** | No truly offline `--dry-run` mode. The bot always talks to Alpaca (and yfinance for data). For unit-testing the loop without API keys, would be useful. | `main.py:run_paper_trading` | Add `--dry-run` flag that constructs a fake broker (no API key) and prints intended trades. Reuses paper_trader's logic. |

### Validation evidence gathered (Audit Round 2)

| Item | Source | Status |
|------|--------|--------|
| `tests/` inventory: 11 files, 75 tests | `pytest --collect-only` | OK |
| `tests/test_paper_trader.py` (mock-heavy, 300 lines) | direct read | doesn't cover switch / NO_ASK / rate-limit |
| `.env.example` untracked (gitignore'd? actually just untracked) | `git ls-files --others` | should be committed for users |
| `data/models/eval_log.csv` exists (Colab eval only) | filesystem | good, but `paper_trades.csvl` is missing |
| `optimizer.AdamW(...)` pt_lr scales | `training/train.py` | per-loss LR multipliers (0.75, 0.5, 0.3) reasonable |
| `portfolio_mse_loss` numerical range | `training/train.py:46` | unbounded inputs; squared numerics concern |
| Walk-forward default (`wf_step_size=1`, `wf_window_size=3`) | `config.py:74-77` | rolling 3-year windows with 1-year step — overlapping numerically but train/val/test within each fold are non-overlapping — OK |
| `_last_business_day()` holiday handling | `src/inference.py:21` | does NOT consult NYSE holidays; weekend walk-back only |
| `cancel_open_orders` rate-limit risk | `src/paper_trader.py:107` | no explicit rate-limit detection / retry |
| `alpaca_paper` toggle visibility | `config.py:99` | no startup lockout / typed-confirmation for LIVE |

### Recommended sequencing (Audit Round 2 PRs)

**PR-OPS-0 (CRITICAL, 1-2h): “silently broken by selection bias”**
1. N-MODEL-LEAK-1 — switch eval_colab Sharpe to `test.npz`; document the change in `eval_log.csv` header

**PR-OPS-1 (HIGH, ~½ day): “don't lose history on crash”**
2. N-LOG-1 — persistent `paper_trades.csvl`
3. N-KILL-1 — kill-switch modal `L` in Textual + --liquidate flag in CLI
4. N-LIVE-LOCKOUT-1 — startup gate for `alpaca_paper=False` with typed `LIVE` confirmation

**PR-OPS-2 (MEDIUM, ~½ day): “hardening, observability, correctness”**
5. N-OBSERVE-1 — rotating file logger config
6. N-MODEL-META-1 — `model_metadata.json` sidecar at every `torch.save`
7. N-PORT-CAP-1 — portfolio-level notional cap (default 0.5)
8. N-EDGE-CASE-1 — explicit `SIDE_UNKNOWN` fallthrough
9. N-DATAQ-1 — holiday calendar (`exchange_calendars` lib or hardcoded list)
10. N-TEST-GAP-1 — fill in test_inference / test_paper_trader / test_data_pipeline gaps
11. N-FEATURE-DEBT-1 — `--tickers-file` flag
12. N-LABEL-CLIP-1 — per-ticker clip option

**PR-OPS-3 (LOW, optional polish):**
- N-RISK-RANGE-1 — USD-based qty
- N-FRACTIONAL-1 — fractional qty support
- N-DRY-RUN-1 — offline broker mock
- N-TICKER-SEC-1 — replace `.` with `-` for Alpaca compat
- N-CACHE-LOCK-1 — atomic write of last_session
- N-LIMIT-ORDERS-1 — limit-order parameter
- N-CORP-ACTIONS-1 — earnings-aware exclusion

### Bottom line (Audit Round 2)

The app is more usable than it is **auditable** and **reproducible**. Shipping a single paper-trading cycle produces **no reproducible artifact**: no metadata for the model picked, no on-disk record of the trades, no observability into the bot's behavior across restarts. The `dad479e` series fixed the obvious bugs but didn't touch the *system-of-trust* questions:
- **Selection bias** in the model-promotion step (N-MODEL-LEAK-1) is the biggest single risk.
- **Auditability**: paper_trades.csvl + rotating log + model_metadata.json form a minimum-viable audit story.
- **Safety**: kill-switch, live-trading lockout, portfolio-level risk cap are non-negotiables for a sanity-checkable deployment.
- **Reproducibility**: scaler+seed+git_sha+date_range in sidecar JSON. Honest Sharpe on `test.npz`, not `val.npz`.

The app should be considered "incomplete but shippable as research code." Reaching "shippable for personal paper-trading with ops-grade trustability" requires PR-OPS-0 + PR-OPS-1 + PR-OPS-2 — about 1.5–2 days of work.

---

## Adversarial Review Pass — 2026-06-27 (validation + drops)

After round 1 (UI/UX) and round 2 (operability/ML), I ran a strict third pass against the actual code on disk. Several plan items turned out to be either **direct false alarms** or **overstated**: their premises don't survive a direct grep against the working tree. This section catalogues what was dropped or downgraded, with the evidence that triggered the drop.

### DROP — premise contradicted by code

| ID | Claim | Why it's actually invalid |
|----|-------|---------------------------|
| **N-CRIT-1** (NEW REGRESSION — `self._model` stays stale on asset toggle) | crypto runs on stale STOCKS-trained weights after `_switch_asset` | **Invalid.** `textual_trader.py` / `trade.py` / `main.py` all call `run_inference(config, ...)` **with no `model=` argument** (grep confirms zero occurrences of `model=self`). Since `src/inference.py:55` falls back to `load_model(config)` when `model is None`, every cycle reloads from disk using the **current** `config.model_save_path`. After `_switch_asset` flips the path to `data/models/crypto/best.pt`, the next call reads crypto weights. *Side observation:* the `M-NEW2` `self._model` cache is unused — it was added in __init__ but never wired through to call sites. Disk-read still happens per cycle. That's a future cleanup, not the same as stale-model risk. |

### DOWNGRADE — severity drop or scope shrink

| ID | Why downgraded |
|----|----------------|
| **N-RISK-RANGE-1** | The "1000× USD-value swing" claim is not a bug. The `trade_buy_qty * ask > max_pos_value` check (reconcile line 174) already trips `MAX_POS_CAP` on $4000 × 10 = $40k positions. Exposure is bounded. The USD-vs-qty framing would be a *feature change* (`trade_buy_qty_usd`), not a correctness fix. **Reclassify as Phase-4 feature** (drop from bug-fix plan). |
| **N-FRACTIONAL-1** | `int` `trade_buy_qty: int = 10` is fine for stocks (Alpaca expects whole-share qty) and fine for crypto default (10 fractional units sent via `qty=int` works for both Alpaca crypto GTC and stock DAY orders). Display uses `int(qty)` which matches what Alpaca receives. No bug. **Drop.** |
| **N-EDGE-CASE-1** | `if pos["side"] == "long" else OrderSide.BUY` (`src/paper_trader.py:185`) already covers shorts: a "short" sale is `BUY` to cover; the else-branch is correct, not a fallthrough bug. **Drop.** (Alpaca positions only ever have `side == "long"` or `"short"`, so the else-branch is well-defined.) |
| **N-TICKER-SEC-1** | Not verified practically. Material claim depends on whether Alpaca rejects `BRK.B` vs `BRK-B` on `submit_order`. Without an actual Alpaca round-trip test, this is a theoretical concern. **Reclassify as Phase-4 (operational hardening).** Cheap to test: insert a `BRK.B` BUY at runtime. |
| **N-CACHE-LOCK-1** | Single-user CLI. Two processes writing simultaneously is not a usage pattern. **Drop.** |

### RECLASSIFY — not bugs, just features / docs

These don't belong on a "fix plan." Move them to a `Phase-4+ future work` section so they still get tracked but don't pollute the bug-fix list.

| ID | Reason |
|----|--------|
| **N-LIMIT-ORDERS-1** | Limit-order support is a feature addition. Alpaca SDK supports it; not currently used. |
| **N-DRY-RUN-1** | Offline broker mock is a feature. Useful for tests but not a bug. |
| **N-CORP-ACTIONS-1** | Earnings-calendar exclusion is a feature / data augmentation. |
| **N-FETCH-FAIL-1** | Failed-tickers audit log is an improvement, not a bug. yfinance skip behavior is documented. |
| **N-STRATEGY-DOCTAG-1** | Architecture diagram is documentation work. |

### KEEP-AS-IS — confirmed by direct grep / read

| ID | Verification |
|----|--------------|
| **N-MODEL-LEAK-1 (CRITICAL)** | `eval_colab.py:78-88` loads `val_targets` from `val.npz`; `sharpe = float(np.mean(port_ret) / np.std(port_ret) * np.sqrt(252))`; `valid.sort(key=lambda x: x[1])` at line 171. Promotion selects by val-Sharpe where val was also used for threshold optimization. **CRITICAL holds.** |
| **UX-N1 (CRITICAL)** | `src/paper_trader.py` lines 148-149 confirm: `for ticker in actionable: self.cancel_open_orders(symbol=ticker)`. With ~480 actionable tickers this is real serial work. |
| **UX-N2 (HIGH)** | grep for `"PAPER TRADING"` / persistent banner — no occurrences anywhere in source. Banner is genuinely absent. |
| **UX-N3 (HIGH)** | `trade.py:195-203` constructs `Live(...)` outside any `with` block; cleanup only runs inside `KeyboardInterrupt`. |
| **N-LIVE-LOCKOUT-1 (HIGH)** | Zero references to `confirm_live`, `live_confirm`, or any startup gate for `alpaca_paper=False`. |
| **N-KILL-1 (HIGH)** | grep for `liquidate`, `panic`, `close_all`, `kill` returns zero custom-code matches. |
| **N-LOG-1 (HIGH)** | grep for `paper_trades`, `trade_history`, `audit_log` returns zero matches in src/. |
| **H-NEW3 (HIGH)** | `trade.py:86`: `pos_str = f"{round(pos['qty'])}"` — confirmed `round()` still used. (`import math` IS at top of file at line 2; this evidence note was stale.) |
| **M-NEW1 (HIGH)** | `textual_trader.py:464`: `pos_str = str(round(pos["qty"])) if pos else "\u2014"` — confirmed. |
| **L-NEW2 (MEDIUM)** | `_switch_asset` (lines 332-348) has no `self._equity_history.clear()` or `self._prev_equity = 0.0`. |
| **N-HIGH-1 (HIGH)** | `self._timer = self.set_interval(...)` first assigned in `on_mount` line 306; `action_interval_up/down` lines 519/526 call `self._timer.stop()` unconditionally. If user mashes `+/-` during startup, AttributeError fires before `_timer` is bound. |
| **M2 (MEDIUM)** | `main.py:128`: `np.savez(f".../fold_{i}_test.npz", ...)`. Wastes 15% of disk. |
| **M1 (MEDIUM)** | `textual_trader.py:13-14`: global `import tqdm.std as _tqdm_std; _tqdm_std.tqdm = _NoopTqdm`. Confirmed. |
| **N-OBSERVE-1 (MEDIUM)** | grep for `FileHandler`/`RotatingFileHandler` in source: zero matches (only inside venv). |
| **N-MODEL-META-1 (MEDIUM)** | grep for `metadata.json`/`model_meta`: zero matches in src/. |
| **N-PORT-CAP-1 (MEDIUM)** | `src/paper_trader.py:155-156` computes `max_pos_value = max(0.0, equity * trade_max_position_pct)`. Per-position cap. No portfolio-level notional accumulator. |
| **N-DATAQ-1 (MEDIUM)** | `_last_business_day()` (`src/inference.py:21`) walks back to weekday but does not consult NYSE holidays. `grep` for `exchange_calendars`/`holiday`: zero matches. |
| **N-TEST-GAP-1 (MEDIUM)** | `tests/test_paper_trader.py` mocks PaperTrader internals; no coverage for asset-switch path, NO_ASK fallback, rate-limit, SIDE_UNKNOWN fallthrough, math.floor match, MAX_POS_CAP boundary. |
| **N-PORTFOLIO-RETURN-1 (MEDIUM)** | `training/train.py:46-52` confirms: `portfolio_return = (pred * target).sum(dim=1)` then `((1 - portfolio_return) ** 2).mean()`. Unbounded inputs squared; gradient explosion risk. |
| **N-FEATURE-DEBT-1 (MEDIUM)** | `grep --tickers-file` returns zero matches. |
| **N-PORTFOLIO-RETURN-1 / N-MSE-DOCS-1 (MEDIUM/LOW)** | Docstring vs formula mismatch is real and confusing for users who read "MSRR" as "Sharpe." KEEP but rename or rewrite the docstring. |

### Severity re-rating summary

After the adversarial pass:
- **1 CRITICAL** holds (was 2): **N-MODEL-LEAK-1**. (Dropped N-CRIT-1.)
- **4 UX items dropped or merged**: UX-N3 moves into PR-OPS-1 (was already in the table).
- **5 items** downgraded or reclassed as **Phase-4 features** (N-LIMIT-ORDERS-1, N-DRY-RUN-1, N-CORP-ACTIONS-1, N-FETCH-FAIL-1, N-STRATEGY-DOCTAG-1, N-RISK-RANGE-1, N-FRACTIONAL-1, N-EDGE-CASE-1, N-TICKER-SEC-1, N-CACHE-LOCK-1).
- **Stale evidence** fixed: H-NEW3 — `import math` IS in trade.py at line 2; only the `round()` rewrite is missing.

### Phase-4+ future work (dropped from bug-fix plan)

These are features / improvements that the user *might* want eventually but aren't blockers. Tracked here so they don't re-flag in future audits.

- **F-NL1** — limit-order support (`LimitOrderRequest`)
- **F-NL2** — `--dry-run` broker mock for offline testing
- **F-NL3** — earnings-day exclusion via `yfinance.Ticker.calendar`
- **F-NL4** — failed-tickers audit log (`data/stocks/_failed.txt`)
- **F-NL5** — architecture diagram in `docs/`
- **F-NL6** — `trade_buy_qty_usd` (USD-based position sizing; requires `qty = usd / ask` math)
- **F-NL7** — verification of `BRK.B` vs `BRK-B` on Alpaca (cheap runtime smoke test)
- **F-NL8** — `fcntl.flock` atomic write of `last_session.json` (multi-process, never used today)

### Recommended order (post-adversarial)

1. **PR-OPS-0 (1-2h, CRITICAL)** — N-MODEL-LEAK-1 only.
2. **PR-BUG-1 (~½ day, HIGH)** — UX-N1 (cancel batch), UX-N3 (Live context), H-NEW3+M-NEW1 (floor), N-HIGH-1 (timer guard), N-LIVE-LOCKOUT-1, N-KILL-1, N-LOG-1.
3. **PR-BUG-2 (~½ day, MEDIUM)** — L-NEW2, N-PORT-CAP-1, N-MODEL-META-1, N-OBSERVE-1, N-FEATURE-DEBT-1, N-TEST-GAP-1, N-EDGE-CASE-1 (collapse to f-string fix or accept as-is), N-DATAQ-1 (use `exchange_calendars`).
4. **PR-CLEAN (~1h, LOW)** — M1 (tqdm scope), M2 (drop test.npz write), N-MSE-DOCS-1 (rename or rewrite docstring), one-liner for `import math` placeholder if appropriate.
5. **PR-WIRE-MODEL-CACHE (~1h)** — thread `model=self._model` through call sites in textual_trader.py / trade.py / main.py so the unused cache actually saves disk loads.
6. **Phase-4 backlog** — F-NL1..F-NL8.

### Bottom line

After the drop pass, the bug-fix plan has **1 CRITICAL** (model-promotion selection bias), **6 HIGH** (cancel race, Live context, round/floor, timer guard, paper/live lockout, no audit file), and **~10 MEDIUM** (L-NEW2 reset, portfolio cap, model metadata, file logger, --tickers-file, test gaps, holiday calendar, etc.). About 1-2 days of focused work to fully retire the bug-fix plan; another ~½ day to make the Phase-4 backlog tractable.


