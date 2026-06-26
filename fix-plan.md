# Fix Plan — Unresolved Items Only

**Last full audit:** 25 commits covering Textual TUI, crypto pipeline, Alpaca/Colab, Rich UI, DDP, threshold/inference refactors.
**Last sync:** commit `d87f3b5` (`fix: address fix-plan.md — C1, H1-H4, M1-M3, L1-L2, N1-N10`, +493/-65 across 12 source files).
**Current validation baseline:** `ruff check` ✅ · `ruff format --check` ✅ · `pytest` 76/76 ✅ · `mypy` import-untyped silenced (N10 ✅).

> _This plan now lists **only items still open** following `d87f3b5`. The original audit (16 closed items) is preserved at the bottom as a "Resolved" reference table for context._

---

## STILL OPEN — Critical & High

> _Zero items in these tiers remain after `d87f3b5`._

None.

---

## STILL OPEN — Medium

### M2. `pretrain.py` `top_head` DDP wrap has no CUDA-only guard (original C2)

**Where:** `training/pretrain.py` lines ~88-92.

```python
top_head = TemporalOrderHead(...).to(device)
if is_distributed():
    top_head = nn.parallel.DistributedDataParallel(
        top_head, device_ids=[device.index] if device.type == "cuda" else None,
    )
```

There is no `if device.type != "cuda": raise RuntimeError(...)` mirroring the guard already in `create_model` (`src/utils.py:34-37`). It is currently masked because `model = create_model(config, device)` runs first and raises on MPS+DDP before reaching this line. So this is a latent footgun that fires the moment `pretrain.py` is refactored to instantiate `top_head` before `create_model`, or when someone adds a "use existing weights" fast path.

**Minimal fix (factor a helper for symmetry):**

```python
# src/utils.py
def wrap_ddp(module: nn.Module, device: torch.device) -> nn.Module:
    if not is_distributed():
        return module
    if device.type != "cuda":
        raise RuntimeError(f"DDP requires CUDA. Got device={device}.")
    return nn.parallel.DistributedDataParallel(module, device_ids=[device.index])
```

Then both `train.py` and `pretrain.py` call `wrap_ddp(model, device)` / `wrap_ddp(top_head, device)`. One canonical guard, two call sites.

**Severity:** **MEDIUM (was CONDITIONAL CRITICAL)** — false positive today, becomes critical on the next refactor.

---

## STILL OPEN — Low

### L3. `textual_trader` globally monkey-patches `tqdm` at import time

**Where:** `textual_trader.py` (top-of-file `_NoopTqdm` block + `_tqdm_std.tqdm = _NoopTqdm`).

Any module that imports `textual_trader` (e.g. `python -c "import textual_trader"`, or a test accidentally importing it) silently replaces the global `tqdm` class for the rest of the process. Downstream tqdm progress bars in tests become no-ops → real failures masquerade as silent successes.

**Minimal fix:** scope the patch inside the App lifecycle.

```python
class TradingApp(App):
    def on_mount(self) -> None:
        self._orig_tqdm = tqdm_module.tqdm
        tqdm_module.tqdm = _NoopTqdm
        # ...existing on_mount body...

    def on_unmount(self) -> None:
        tqdm_module.tqdm = self._orig_tqdm
```

**Severity:** **LOW** — debug & test hygiene, not a runtime correctness bug.

---

### L4. `_NoopTqdm.write` references `sys` defined later in the same module

**Where:** `textual_trader.py` lines ~8-32.

`write()` body uses `sys.stderr`, and `import sys` is in the same file but below the class. Works by accident because Python evaluates the function body lazily on first call, not at class instantiation. A future refactor (`import sys` moved, file split, conditional import) breaks this silently.

**Minimal fix:** move `import sys` to the top of the file, immediately after `from textual import …`. Or pin it inside `class _NoopTqdm` as a default arg `sys=sys_imported_above`.

**Severity:** **LOW** — order-fragility only.

---

### L5. `cfg.tickers = list(range(N))` in `eval_colab` is a confusing stopgap

**Where:** `eval_colab.py` line ~60 (the line still works, just confusing).

Setting `cfg.tickers = [0, 1, 2, ..., N-1]` is a placeholder that exploits the `stock_embed` model's tolerance for integer indices. Readers see `cfg.tickers = [0, 1, 2, …]` and reasonably conclude the model now takes numeric tickers everywhere.

**Minimal fix:** introduce a thin helper that signals intent.

```python
# config.py
def set_n_stocks(cfg: "Config", n: int) -> None:
    """Override stock-universe size without mutating cfg.tickers (placeholder indices only)."""
    cfg.n_stocks = n  # used by stock_model.Emb(n_stocks)
```

`eval_colab.py` becomes `set_n_stocks(cfg, val_features.shape[1])`. No `cfg.tickers` mutation.

**Severity:** **LOW** — code-clarity / future-proofing.

---

## STILL OPEN — Nitpicks

### N2. `src/features.py _data_hash` hashes filename + mtime + size, not CSV content

**Where:** `src/features.py` (post-`d87f3b5` `_data_hash`).

Replacing a CSV with byte-identical content preserves the hash → cache hit on logically different data. Unlikely in practice (raw_data cache is append-only), but cheap to defend.

**Minimal fix:** add a CRC32 over the first/last 4 KB.

```python
import zlib

def _data_hash(data_dir: str) -> str:
    parts = []
    for p in sorted(Path(data_dir).glob("*.csv")):
        h = hashlib.md5()
        h.update(p.read_bytes()[:4096])
        with p.open("rb") as f:
            f.seek(-4096, 2)
            h.update(f.read())
        parts.append(f"{p.name}|m={p.stat().st_mtime}|s={p.stat().st_size}|h={h.hexdigest()[:8]}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()
```

**Severity:** NIT — defensive only.

---

### N4. `walk-forward` writes `fold_i_test.npz` but no consumer reads it

**Where:** `main.py` `prepare_walk_forward_splits` still creates the file even though only `fold_i_train.npz` / `fold_i_val.npz` are loaded downstream (post-`d87f3b5`).

~15% wasted disk per walk-forward build. Trivial fix — drop the file or comment why it persists (debugging convenience for ad-hoc eval).

**Minimal fix:** either delete the test save block, or add a comment "kept for ad-hoc re-eval — consumers run via `--resume --model fold_<i>` which loads this".

**Severity:** NIT.

---

### N7. `reconcile` BUY logic doesn't expose the "no pyramid" rule

**Where:** `src/paper_trader.py` `reconcile()` (unchanged in `d87f3b5`).

If BUY fires and `has_pos` is true, the position is duplicated (`held + qty`). The code is correct given momentum-style signal averaging, but readers will wonder why we don't gate on `has_pos`. A 1-line comment explaining the intentional averaging-on-add rule covers it.

**Minimal fix:** `reconcile()` BUY branch — add comment above the position-cap check:

```python
# BUY intentionally adds to held positions (signal averaging by design).
# To enforce "no pyramid", gate here on `held == 0` before the cap check.
```

**Severity:** NIT — docs.

---

### N8. `textual_trader._fstring` uses inline table text — `tqdm`'s `_from_url` macro could shrink by ~30 lines

**Where:** `textual_trader.py`.

Minor — included only because ruff `tab` already accepts the macro form and `d87f3b5` touched this file. Pure readability.

**Severity:** NIT.

---

## STILL OPEN — UX / UI (entire research-backed workstream)

> _None of the UX items from the earlier research pass have been addressed. The bug-fix commit was orthogonal — this is the next PR stream. Listed in priority order; impact × risk in legend._

### Bucket A — Drop-in Textual widgets (highest payoff, smallest diff)

| ID | Item | Where | Status (after d87f3b5) |
|----|------|-------|------------------------|
| **UX1** | Replace manual `_sparkline()` string-builder with built-in `Sparkline` | `textual_trader.py` — drop `_sparkline` method | **OPEN** |
| **UX2** | `Digits` widget for Equity/Cash headline numbers | `MetricCard` for equity/cash | **OPEN** |
| **UX3** | `RichLog` panel for trade audit trail (`[HH:MM:SS] BUY 10 AAPL @ $X`) | `compose` below `DataTable` | **OPEN** |
| **UX4** | `ProgressBar` (indeterminate) for "inference running" | Top right of metric row | **OPEN** |
| **UX5** | `TabbedContent` for `/Stocks/Crypto/Tuning` tabs (replaces the two `Button`s) | Top asset row | **OPEN** |
| **UX6** | `Collapsible` around the threshold-tuning controls | Tuning tab | **OPEN** |
| **UX7** | `MarkdownViewer` for an in-app "Strategy Notes" screen | New screen via `App.SCREENS` | **OPEN** |

### Bucket B — Real-time feedback / staleness (no new infrastructure)

| ID | Item | Where | Status |
|----|------|-------|--------|
| **UX8** | Bloomberg-style flash on equity change | `MetricCard.watch_value` | **OPEN** |
| **UX9** | "Last refreshed Xm ago" indicator in header | `Header` right slot | **OPEN** |
| **UX10** | Row-level position-size visual (yellow → red as % of cap) | `_update_table` per-row | **OPEN** |
| **UX11** | Color-blind-safe pairings: P&L column uses `▲`/`▼` alongside red/green | `_update_table` P&L formatting | **OPEN** |
| **UX12** | Tooltips on the metric cards + main buttons | `MetricCard.__init__` + Buttons | **OPEN** |
| **UX13** | De-duplicated error toasts | `_refresh_cycle` error branch | **OPEN** |
| **UX14** | Hotkey hint footer bar (lazygit-style) | Replace/augment `Footer` | **OPEN** |

### Bucket C — Confirmation / safety UX

| ID | Item | Where | Status |
|----|------|-------|--------|
| **UX15** | Liquidate-all confirmation modal (L → Y/Enter required) | New `LiquidateModal` class + binding | **OPEN** |
| **UX16** | Threshold-bound arrow-key confirmation (>0.95 prompts) | `action_threshold_up` | **OPEN** |
| **UX17** | Disconnect-strike indicator on market-dot (3 consecutive errors) | `_refresh_cycle` error branch | **OPEN** |

### Bucket D — Architectural roadmap (separate PR)

| ID | Item | Effort | Status |
|----|------|--------|--------|
| **UX18** | Alpaca WebSocket: `TradingStream.subscribe_trade_updates` replaces 15-min REST poll | 2-3d | **OPEN** |
| **UX19** | Live market data stream (`StockDataStream`) for active positions | 1-2d | **OPEN** |
| **UX20** | Multi-screen app stack (Dashboard / Tuning / Logs / Backtest / Notes) | 1d | **OPEN** |
| **UX21** | User-configurable DataTable columns (saved to `~/.tradingbot/layout.json`) | 1d | **OPEN** |
| **UX22** | Strategy inspector (Model output vs consensus) | 0.5d | **OPEN** |

### Bucket E — Quick misc (≤5 lines each)

| ID | Item | Status |
|----|------|--------|
| **UX23** | Add `Ctrl+P` binding + visible hint in `HelpScreen` | **OPEN** |
| **UX24** | Right-align numeric columns (Pos, %, Score, P&L) | **OPEN** |
| **UX25** | Persist `interval`/`buy_t`/`sell_t` to `data/last_session.json` | **OPEN** |
| **UX26** | Refresh `equity_history` on asset-class switch (currently cross-pollutes) | **OPEN** |
| **UX27** | Fault-tolerant status bar (already partially done in `_refresh_buttons` — extend to header) | **OPEN** |
| **UX28** | "Last filled:" divider line in RichLog feed | **OPEN** |

---

## False positives (closed as noise)

These were candidates from the original audit that, after a closer read, are *not* real bugs. They are kept here so future audits don't re-litigate them.

| ID | Candidate | Verdict |
|----|-----------|---------|
| F1.1 | `math.floor` round-trip fractional shares | Confirmed correct (now `N1` resolved too via `min(...)` simplification) |
| F1.2 | `compute_features_for_date` uses BTC/USD for `market_state` | Not leakage — same pattern as SPY for stocks; intentional |
| F1.3 | DDP scaler rank-local stats | False positive — same npz on every rank; broadcast block was dead code (now **removed**, see Resolved table) |
| F1.4 | `textual_trader` worker coordination | Now fully mitigated by `exclusive=True` (see L2 in Resolved) |
| F1.5 | `cancel_open_orders` blanket-cancel | Signature now requires `symbol` (see H4 in Resolved) |

---

## Resolved by `d87f3b5` (reference only)

The following items are **closed** by commit `d87f3b5` and no longer need attention. Kept here so that re-auditing the same area doesn't surface them.

| ID | Title | Where | Resolution |
|----|-------|-------|------------|
| **C1** | `_fold_metadata()` fingerprint missing tickers/asset_class | `main.py` | Added `asset_class`, `crypto_pairs`, `n_stocks` to fingerprint |
| **H1** | `time.sleep` negative clamp | `main.py`, `trade.py` | Wrapped as `max(0.0, min(wait, …))` |
| **H2** | Crypto feature cache overwrites stock cache | `src/features.py` | `cache_dir` parameter threaded through `save_cached_features` / `load_cached_features` |
| **H3** | `BUY` falls through when `ask=None` | `src/paper_trader.py` | Refuses trade, appends `(ticker, 0, "NO_ASK")`; test updated |
| **H4** | `cancel_open_orders(symbol=None)` blanket-cancel footgun | `src/paper_trader.py` | Signature tightened to `symbol: str`; dead branch dropped |
| **M1** | Threshold scan O(N²) on large universes | `training/threshold.py` | Grid spacing coarsened to 0.05 |
| **M2** | DDP scaler comment wrong + broadcast redundant | `training/train.py` | Broadcast block removed; comment now correct |
| **M3** | `_raw_data_cache` unbounded | `src/inference.py` | Cache cleared when `len > 1` (today-only) |
| **L1** | `eval_colab` hardcoded `cuda/cpu`, no MPS | `eval_colab.py` | Uses `get_device()` from config |
| **L2** | `textual_trader` workers not exclusive | `textual_trader.py` | `run_worker(..., name="cycle", exclusive=True)` |
| **N1** | `min(held, sell_qty) if held > sell_qty else held` verbose | `src/paper_trader.py` | Simplified to `min(held, sell_qty)` |
| **N3** | test name typo "blancket" | `tests/test_paper_trader.py` | Test renamed to `test_cancel_requires_symbol` |
| **N5** | `threshold.txt` non-atomic write | `training/threshold.py` | `tmp.rename(target)` atomic pattern |
| **N6** | `load_scaler` no JSON-structure validation | `src/utils.py` | `assert len(data["mean"]) > 0` |
| **N9** | `cancel_open_orders` extra API call when no orders | `src/paper_trader.py` | Single-symbol query path; dead branch dropped |
| **N10** | mypy noise floor from `import-untyped` | `pyproject.toml` | `disable_error_code = ["import-untyped"]` added |

---

## Recommended order (remaining open items)

### Bug-fix PR (P0 quality bar, ~½ day)

1. **M2** (C2) pretrain DDP guard — 3 lines, but factor `wrap_ddp()` helper for symmetry.
2. **L5** `cfg.tickers = list(range(N))` stopgap — `set_n_stocks()` helper.
3. **L3** tqdm global monkey-patch — scope to App lifecycle.
4. **L4** `_NoopTqdm.write` sys import order — move `import sys` to top.
5. **N2** `_data_hash` adds CRC — ~10 lines, defensive.
6. **N4** drop unused `fold_i_test.npz` save — 1-line.
7. **N7 / N8** commentary + macro cleanup — drive-by in the same PR.

### UI workstream (separate PRs)

| PR | Items | Wall time |
|----|-------|-----------|
| **PR-UX1** | UX1, UX2, UX3, UX4 — drop-in Textual widgets + RichLog + Digits | ~1 day |
| **PR-UX2** | UX5, UX6, UX8, UX9 — tabs/collapsible/flash/staleness | ~0.5 day |
| **PR-UX3** | UX10, UX11, UX15, UX16, UX17 — risk-visualization + safety confirmations | ~0.5 day |
| **PR-UX4** | UX12, UX13, UX14, UX23-UX28 — a11y/persistence/footer hints | ~0.5 day |
| **PR-UX5** (architecture) | UX18, UX19 — WebSocket migration | ~1 week |
| **PR-UX6** (future) | UX20, UX21, UX22 — multi-screen app + config + inspector | ~2 days |

---

## Reference URLs (preserved from prior audit)

- Textual widget gallery: <https://textual.textualize.io/widget_gallery/>
- Textual reactivity / workers / screens: <https://textual.textualize.io/guide/reactivity/> · <https://textual.textualize.io/guide/workers/> · <https://textual.textualize.io/guide/screens/>
- K9s (TUI keyboard conventions): <https://github.com/derailed/k9s>
- Lazygit (TUI menu patterns): <https://github.com/jesseduffield/lazygit>
- Helix (modal key-mappings): <https://helix-editor.com/>
- Alpaca WebSocket streaming docs: <https://docs.alpaca.markets/us/docs/websocket-streaming>
- Alpaca MCP server: <https://github.com/alpacahq/alpaca-mcp-server>
- Lollypop Design — Trading App Design Guide 2026: <https://lollypop.design/blog/2026/june/trading-app-design/>
