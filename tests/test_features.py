import numpy as np
import pandas as pd

from src.features import (
    build_targets,
    compute_bollinger,
    compute_features_for_date,
    compute_macd,
    compute_market_state,
    compute_rsi,
    compute_window_features,
    normalize_targets_cross_sectional,
)


def _fake_stock_data(length=300):
    dates = pd.date_range("2020-01-01", periods=length, freq="D")
    return pd.DataFrame(
        {
            "Close": np.random.randn(length).cumsum() + 100,
            "High": np.random.randn(length).cumsum() + 101,
            "Low": np.random.randn(length).cumsum() + 99,
            "Volume": np.random.randint(1e6, 5e6, size=length),
            "Open": np.random.randn(length).cumsum() + 100,
        },
        index=dates,
    )


def test_compute_rsi() -> None:
    close = pd.Series(np.random.randn(100).cumsum() + 100)
    rsi = compute_rsi(close, period=14)
    assert len(rsi) == 100
    assert rsi.max() <= 100
    assert rsi.min() >= 0


def test_compute_macd() -> None:
    close = pd.Series(np.random.randn(100).cumsum() + 100)
    macd = compute_macd(close)
    assert list(macd.columns) == ["macd", "macd_signal", "macd_hist"]
    assert len(macd) == 100


def test_compute_bollinger() -> None:
    close = pd.Series(np.random.randn(100).cumsum() + 100)
    bb = compute_bollinger(close)
    assert list(bb.columns) == ["bb_upper", "bb_lower", "bb_pct_b"]
    assert len(bb) == 100


def test_compute_window_features() -> None:
    df = _fake_stock_data(300)
    features = compute_window_features(df)
    assert len(features) == 300
    assert "sma_20" in features.columns
    assert "rsi_14" in features.columns
    assert "macd" in features.columns
    assert "bb_pct_b" in features.columns
    assert "volatility_21" in features.columns
    assert "volume_ratio" in features.columns
    assert "intraday_range" in features.columns
    assert "return_1d" in features.columns
    assert "max_drawdown" in features.columns


def test_build_targets_shape() -> None:
    dates = pd.date_range("2020-01-01", periods=100, freq="D")
    tickers = ["AAPL", "MSFT"]
    raw_data = {t: _fake_stock_data(120) for t in tickers}
    targets = build_targets(raw_data, tickers, [str(d) for d in dates], max_return=0.05)
    assert targets.shape == (100, 2), f"Expected (100, 2), got {targets.shape}"


def test_build_targets_range() -> None:
    dates = pd.date_range("2020-01-01", periods=50, freq="D")
    tickers = ["AAPL"]
    raw_data = {"AAPL": _fake_stock_data(70)}
    targets = build_targets(raw_data, tickers, [str(d) for d in dates], max_return=0.05)
    assert targets.min() >= -1.0
    assert targets.max() <= 1.0


def test_build_targets_extreme_clipping() -> None:
    dates = pd.date_range("2020-01-01", periods=50, freq="D")
    tickers = ["AAPL"]
    raw_data = {"AAPL": _fake_stock_data(70)}
    targets_raw = build_targets(
        raw_data, tickers, [str(d) for d in dates], max_return=0.05, clip_extreme=False
    )
    targets_clipped = build_targets(
        raw_data, tickers, [str(d) for d in dates], max_return=0.05, clip_extreme=True
    )
    assert targets_clipped.max() <= targets_raw.max()
    assert targets_clipped.min() >= targets_raw.min()


def test_build_targets_missing_ticker() -> None:
    dates = pd.date_range("2020-01-01", periods=30, freq="D")
    tickers = ["AAPL", "MISSING"]
    raw_data = {"AAPL": _fake_stock_data(50)}
    targets = build_targets(raw_data, tickers, [str(d) for d in dates], max_return=0.05)
    assert targets.shape == (30, 2)
    assert np.all(targets[:, 1] == 0.0)


def test_normalize_targets_cross_sectional() -> None:
    targets = np.array([[0.1, -0.05, 0.2, -0.1], [0.01, 0.02, -0.03, 0.0]])
    normed = normalize_targets_cross_sectional(targets, winsorize_pct=0.0)
    for i in range(2):
        assert abs(np.mean(normed[i])) < 1e-6
        assert abs(np.std(normed[i]) - 1.0) < 1e-4


def test_compute_market_state() -> None:
    dates = pd.date_range("2020-01-01", periods=300, freq="D")
    raw = {"SPY": _fake_stock_data(300)}
    state = compute_market_state(raw, [str(d) for d in dates])
    assert state.shape == (300, 5)
    assert not np.any(np.isnan(state))


def test_compute_market_state_missing_spy() -> None:
    raw = {"AAPL": _fake_stock_data(100)}
    state = compute_market_state(raw, ["2020-06-01"])
    assert state.shape == (1, 5)
    assert np.all(state == 0.0)


def test_compute_features_for_date() -> None:
    dates = pd.date_range("2020-01-01", periods=300, freq="D")
    raw = {"AAPL": _fake_stock_data(300)}
    feats, tickers = compute_features_for_date(raw, str(dates[-1].date()))
    assert feats.shape == (1, 120)
    assert tickers == ["AAPL"]
    assert not np.any(np.isnan(feats))
