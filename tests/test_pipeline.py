import numpy as np
import pandas as pd

from config import Config
from src.features import build_feature_matrix, build_targets, compute_features_for_date
from training.train import train_seed


def _make_synthetic_data(n_stocks=5, n_days=500):
    dates = pd.date_range("2020-01-01", periods=n_days, freq="D")
    tickers = [f"STOCK_{i}" for i in range(n_stocks)]
    data = {}
    for t in tickers:
        close = np.random.randn(n_days).cumsum() + 100
        data[t] = pd.DataFrame(
            {
                "Close": close,
                "High": close * 1.02,
                "Low": close * 0.98,
                "Open": close * 0.99,
                "Volume": np.random.randint(1e6, 5e6, n_days),
            },
            index=dates,
        )
    return data, tickers, dates


def test_build_feature_matrix_synthetic() -> None:
    data, tickers, _dates = _make_synthetic_data()
    features, out_tickers, _out_dates = build_feature_matrix(data)
    assert features.shape[0] > 0
    assert len(out_tickers) == len(tickers)
    assert not np.any(np.isnan(features))


def test_build_targets_synthetic() -> None:
    data, tickers, dates_list = _make_synthetic_data()
    dates = [str(d) for d in dates_list]
    targets = build_targets(data, tickers, dates, max_return=0.05)
    assert targets.shape == (len(dates), len(tickers))
    assert targets.min() >= -1.0
    assert targets.max() <= 1.0


def test_pipeline_end_to_end() -> None:
    n_stocks, n_days = 5, 300
    data, _tickers, _all_dates = _make_synthetic_data(n_stocks, n_days)
    features, out_tickers, out_dates = build_feature_matrix(data)
    targets = build_targets(data, out_tickers, out_dates, max_return=0.05)
    split = int(len(out_dates) * 0.7)
    cfg = Config(tickers=out_tickers, max_epochs=50, early_stop_patience=10)
    model, scaler = train_seed(
        cfg,
        features[:split],
        targets[:split],
        features[split:],
        targets[split:],
        seed=42,
        loss_mode="mse",
    )
    assert model is not None
    assert scaler is not None


def test_pipeline_msrr_loss() -> None:
    n_stocks, n_days = 5, 300
    data, _tickers, _all_dates = _make_synthetic_data(n_stocks, n_days)
    features, out_tickers, out_dates = build_feature_matrix(data)
    targets = build_targets(data, out_tickers, out_dates, max_return=0.05)
    split = int(len(out_dates) * 0.7)
    cfg = Config(tickers=out_tickers, max_epochs=50, early_stop_patience=10)
    model, _scaler = train_seed(
        cfg,
        features[:split],
        targets[:split],
        features[split:],
        targets[split:],
        seed=42,
        loss_mode="msrr",
    )
    assert model is not None


def test_compute_features_for_date_integration() -> None:
    data, tickers, dates = _make_synthetic_data(3, 300)
    feats, out_tickers = compute_features_for_date(data, str(dates[-1].date()))
    assert feats.shape[0] == len(tickers)
    assert len(out_tickers) == len(tickers)
    assert not np.any(np.isnan(feats))
