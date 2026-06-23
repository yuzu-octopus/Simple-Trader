# Codebase Bugfix & Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all critical bugs, important issues, and minor code quality problems found in the comprehensive review.

**Architecture:** Each task touches a single file or closely related files. Tasks are independent enough to be worked in parallel, but ordered by impact — critical correctness fixes first, then important issues, then minor cleanup.

**Tech Stack:** Python 3.14, PyTorch 2.12, numpy, pandas, ruff

**Plan location:** `docs/compose/plans/2026-06-23-codebase-fixes.md`

---

### Task 1: Fix window feature stacking (features.py — Critical)

**Issue:** `src/features.py:213-230` — All four window slices use `w.iloc[-1]` which always returns the same last row. The 120-feature vector is just the 30 base features repeated 4×, making the multi-window design non-functional.

**Fix:** Replace `w.iloc[-1]` (last value, identical across windows) with `w.mean()` (average across the window's lookback period). Each window size (1y/1m/1w/1d) now produces genuinely different feature vectors.

**Files:**
- Modify: `src/features.py`
- Test: `tests/test_features.py`

- [ ] **Step 1: Fix `build_feature_matrix` window extraction**

In `src/features.py`, inside the `_extract_date` closure (around line 220-230), change the inner loop from taking `w.iloc[-1]` to `w.mean()`:

```python
# Change from:
for w in windows:
    if len(w) == 0:
        stock_vec.extend([0.0] * N_FEATURES)
    else:
        latest = w.iloc[-1]
        stock_vec.extend(
            [
                float(latest[col]) if not pd.isna(latest[col]) else 0.0
                for col in series.columns[:N_FEATURES]
            ]
        )

# Change to:
for w in windows:
    if len(w) == 0:
        stock_vec.extend([0.0] * N_FEATURES)
    else:
        w_mean = w.mean()
        stock_vec.extend(
            [
                float(w_mean[col]) if not pd.isna(w_mean[col]) else 0.0
                for col in series.columns[:N_FEATURES]
            ]
        )
```

- [ ] **Step 2: Fix `compute_features_for_date` same pattern**

In `src/features.py`, `compute_features_for_date` function (around line 273-284), apply the same fix — replace `w.iloc[-1]` with `w.mean()`.

- [ ] **Step 3: Verify tests pass**

Run: `uv run pytest tests/test_features.py tests/test_pipeline.py -v`
Expected: All tests pass (existing tests check shapes/non-nan, not specific window values)

---

### Task 2: Fix market state batch indexing (train.py — Critical)

**Issue:** `training/train.py:186-188` — `train_m_t` is indexed by `step * config.batch_size`, assuming sequential batch order. But `DataLoader(shuffle=True)` randomizes batches, causing the market state tensor to be applied to wrong dates. Silently corrupts training when `market_state_size > 0`.

**Fix:** Include `train_m_t` in the `TensorDataset` so it stays shuffled in sync with features/targets.

**Files:**
- Modify: `training/train.py`

- [ ] **Step 1: Include market state in TensorDataset**

Replace the DataLoader creation (around line 111-115):

```python
# Change from:
train_loader = DataLoader(
    TensorDataset(train_t, train_y),
    batch_size=config.batch_size,
    shuffle=True,
)

# Change to:
if train_m_t is not None:
    train_loader = DataLoader(
        TensorDataset(train_t, train_y, train_m_t),
        batch_size=config.batch_size,
        shuffle=True,
    )
else:
    train_loader = DataLoader(
        TensorDataset(train_t, train_y),
        batch_size=config.batch_size,
        shuffle=True,
    )
```

- [ ] **Step 2: Update training loop to use dataset-provided market state**

Replace the existing batch unpacking + manual market_state indexing (around lines 182-191):

```python
# Change from:
for step, (batch_x, batch_y) in enumerate(train_loader):
    batch_x, batch_y = batch_x.to(device), batch_y.to(device)
    with torch.autocast(device_type=device.type, enabled=use_amp):
        if train_m_t is not None:
            batch_m = train_m_t[
                step * config.batch_size : (step + 1) * config.batch_size
            ].to(device)
            pred = model(batch_x, market_state=batch_m)
        else:
            pred = model(batch_x)
        loss = criterion(pred, batch_y) / grad_accum_steps

# Change to:
for step, batch in enumerate(train_loader):
    if train_m_t is not None:
        batch_x, batch_y, batch_m = batch
        batch_x, batch_y, batch_m = batch_x.to(device), batch_y.to(device), batch_m.to(device)
    else:
        batch_x, batch_y = batch
        batch_x, batch_y = batch_x.to(device), batch_y.to(device)
    with torch.autocast(device_type=device.type, enabled=use_amp):
        if train_m_t is not None:
            pred = model(batch_x, market_state=batch_m)
        else:
            pred = model(batch_x)
        loss = criterion(pred, batch_y) / grad_accum_steps
```

- [ ] **Step 3: Verify tests pass**

Run: `uv run pytest -v`
Expected: All 38+ existing tests pass

---

### Task 3: Fix build_targets argument in walk-forward path (main.py — Critical)

**Issue:** `main.py:308` — `build_targets(config.tickers, config.tickers, ...)` passes a list as `raw_data` (should be dict). Crashes with `AttributeError` when walk-forward mode is used without pre-cached features.

**Fix:** Save the `raw_data` dict from the `fetch_stock_data` call and pass it to `build_targets`.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Fix the build_targets call**

Replace lines ~298-310:

```python
# Change from:
if n_folds == 0:
    features, tickers, dates = build_feature_matrix(
        fetch_stock_data(
            config.tickers,
            config.train_start,
            config.test_end,
            config.raw_data_path,
        )
    )
    config.tickers = tickers
    targets = build_targets(
        config.tickers, config.tickers, dates, config.label_max_return
    )
    n_folds = prepare_walk_forward_splits(features, targets, dates, config)

# Change to:
if n_folds == 0:
    raw_data = fetch_stock_data(
        config.tickers,
        config.train_start,
        config.test_end,
        config.raw_data_path,
    )
    features, tickers, dates = build_feature_matrix(raw_data)
    config.tickers = tickers
    targets = build_targets(
        raw_data, config.tickers, dates, config.label_max_return
    )
    n_folds = prepare_walk_forward_splits(features, targets, dates, config)
```

- [ ] **Step 2: Verify syntax**

Run: `uv run python -c "import main; print('OK')"`
Expected: No import errors

---

### Task 4: Remove test-set threshold optimization (main.py — Important)

**Issue:** `main.py:346-353` — Second threshold optimization runs on test set (data leakage — test data should be held out for final evaluation only). Results are also discarded (no save, no effect).

**Fix:** Delete lines 345-353 (the test set evaluation block).

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Remove the test-set evaluation block**

Delete these lines (around 345-353):

```python
print("\n=== Test Set Evaluation ===")
with np.load(f"{config.features_path}/test.npz") as data:
    test_features = data["features"]
    test_targets = data["targets"]
from src.utils import load_model
from training.threshold import optimize_threshold

model_test = load_model(config)
optimize_threshold(config, model_test, test_features, test_targets)
```

- [ ] **Step 2: Verify syntax**

Run: `uv run python -c "import main; print('OK')"`

---

### Task 5: Remove dead code `split_dates_within` (main.py — Important)

**Issue:** `main.py:20-36` — Function `split_dates_within` is defined but never called anywhere.

**Fix:** Remove the entire function definition.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Remove dead function**

Delete `split_dates_within` function (lines 20-36). Also remove the unused `train_idx`/`val_idx`/`test_idx` parameters.

- [ ] **Step 2: Verify syntax**

Run: `uv run python -c "import main; print('OK')"`

---

### Task 6: Simplify force-features cleanup logic (main.py — Minor)

**Issue:** `main.py:284-285` — The expression `args.force_features and has_features or not has_features` is convoluted. It's logically equivalent to `args.force_features or not has_features`. Also, the cache dir is deleted even when features don't exist (wasteful `mkdir` + delete).

**Fix:** Simplify the boolean expression, only clear cache when `--force-features` is used.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Fix the logic**

Replace lines 283-289:

```python
# Change from:
if args.force_features or not has_features:
    if args.force_features and has_features or not has_features:
        cache_dir = Path(config.features_path)
        if cache_dir.exists():
            for p in cache_dir.glob("*"):
                p.unlink()
    n_folds = prepare_data(config)

# Change to:
if args.force_features or not has_features:
    if args.force_features:
        cache_dir = Path(config.features_path)
        if cache_dir.exists():
            for p in cache_dir.glob("*"):
                p.unlink()
    n_folds = prepare_data(config)
```

- [ ] **Step 2: Verify syntax**

Run: `uv run python -c "import main; print('OK')"`

---

### Task 7: Fix `os.unlink` → `Path.unlink` in config.py (Minor)

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Replace os.unlink with Path.unlink**

Remove `import os` at line 3, and change `os.unlink(tmp)` at line 28 to `Path(tmp).unlink()`.

- [ ] **Step 2: Verify**

Run: `uv run python -c "from config import Config; print('OK')"`

---

### Task 8: Fix `open()` → `Path.open()` in utils.py (Minor)

**Files:**
- Modify: `src/utils.py`

- [ ] **Step 1: Replace open with Path.open in save_scaler**

```python
# Change from:
def save_scaler(scaler: StandardScaler, path: str) -> None:
    with open(path, "w") as f:
        json.dump({"mean": scaler.mean_.tolist(), "var": scaler.var_.tolist()}, f)

# Change to:
def save_scaler(scaler: StandardScaler, path: str) -> None:
    Path(path).write_text(
        json.dumps({"mean": scaler.mean_.tolist(), "var": scaler.var_.tolist()})
    )
```

- [ ] **Step 2: Replace open with Path.open in load_scaler**

```python
# Change from:
def load_scaler(path: str) -> StandardScaler:
    with open(path) as f:
        data = json.load(f)

# Change to:
def load_scaler(path: str) -> StandardScaler:
    data = json.loads(Path(path).read_text())
```

Add `from pathlib import Path` at top if not already present.

- [ ] **Step 3: Verify**

Run: `uv run python -c "from src.utils import save_scaler, load_scaler; print('OK')"`

---

### Task 9: Clean up remaining ruff issues (Minor)

**Files:**
- Modify: `config.py`, `main.py`, `src/features.py`, `tests/*.py`, `training/train.py`

- [ ] **Step 1: Run ruff auto-fix**

```bash
uv run ruff check --fix .
```

Expected: 68+ remaining issues auto-fixable.

- [ ] **Step 2: Run ruff format check**

```bash
uv run ruff format --check .
```

Expected: No formatting errors.

- [ ] **Step 3: Fix global ruff config warnings**

The ruff config at `~/.config/ruff/ruff.toml` has deprecated top-level settings. Replace `select = ["ALL", "I"]` with `[lint]` section header and `ignore` with `lint.ignore`. Also fix remapped rule names (`TCH001` → `TC001`, etc.).

Actually these are user-level config and might be shared across projects — only fix if the user requests it. Document the issue instead.

---

### Task 10: Fix unused `os` import in config.py (Minor)

**Note:** Already partially covered in Task 7 (removing `os.unlink` removes the need for `import os`).

---

### Task 11: Fix dead code in `for label` loop (main.py — Minor)

**Issue:** `main.py:61` — Variable `label` in `for i, (tr, va, te, label) in enumerate(folds):` is unused.

**Fix:** Replace with `_label` or just `_`.

```python
# Change from:
for i, (tr, va, te, label) in enumerate(folds):
# Change to:
for i, (tr, va, te, _label) in enumerate(folds):
```

---

### Task 12: Fix unused `S` variable in features.py (Minor)

**Issue:** `src/features.py:91` — `T, S = targets.shape` — `S` is never used.

**Fix:** Replace `T, S` with `T, _` or `T, _S`.

---

### Task 13: Add tests for threshold optimization and inference (Test gap — Important)

- [ ] **Step 1: Add test for `optimize_threshold`**

```python
# tests/test_training.py
def test_optimize_threshold_runs():
    from training.threshold import optimize_threshold
    model = StockTransformer(n_stocks=5, n_features=120, d_model=32, nhead=2, num_layers=1)
    val_features = np.random.randn(20, 5, 120)
    val_targets = np.random.randn(20, 5)
    buy_t, sell_t = optimize_threshold(Config(), model, val_features, val_targets)
    assert 0 <= buy_t < 0.5
    assert 0 <= sell_t < 0.5
```

- [ ] **Step 2: Add test for `split_dates_within`** — skip since we're removing that function.

- [ ] **Step 3: Verify tests**

Run: `uv run pytest tests/ -v`
Expected: All tests pass

---

### Full Verification

- [ ] **Run all tests**

```bash
uv run pytest -v --tb=short
```

Expected: All tests pass

- [ ] **Run ruff**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: Clean output (no errors)
