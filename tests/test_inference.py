"""Tests for src/inference.py."""

import itertools
from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import torch

from config import Config
from src.inference import (
    _last_business_day,
    invalidate_inference_cache,
    run_inference,
)


def test_last_business_day_returns_weekday() -> None:
    d = date.fromisoformat(_last_business_day())
    assert d.weekday() < 5


def test_last_business_day_format() -> None:
    parts = _last_business_day().split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 4


def _tiny_ohlcv() -> pd.DataFrame:
    """One-day OHLCV frame with a real DatetimeIndex so .index works."""
    idx = pd.to_datetime(["2025-01-14"])
    return pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000.0],
        },
        index=idx,
    )


def _build_inference_mocks() -> tuple[MagicMock, MagicMock, np.ndarray]:
    """Build the model + scaler mocks that run_inference exercises.

    Returns a (model_mock, scaler_mock, features_mock) tuple where
    features_mock is a (1, 1, 120) float32 array matching the default
    config's n_features.
    """
    model_mock = MagicMock()
    # itertools.cycle gives an *infinite* iterator, which is what next()
    # needs (a list is an iterable, not an iterator, and would raise
    # TypeError on next()). Each .parameters() call returns the same cycle.
    model_mock.parameters.return_value = itertools.cycle([torch.zeros(1)])
    chain = MagicMock()
    chain.numpy.return_value = np.zeros((1, 1))
    chain.cpu.return_value = chain
    chain.to.return_value = chain
    model_mock.return_value = chain

    scaler_mock = MagicMock()
    scaler_mock.transform = MagicMock(side_effect=lambda x: x)

    features_mock = np.zeros((1, 1, 120), dtype=np.float32)
    return model_mock, scaler_mock, features_mock


def test_run_inference_fetches_once_per_date() -> None:
    """Same (tickers, date) must reuse the cache; a different date forces refetch.

    Without date-keying, a long-running trading loop would silently reuse
    yesterday's OHLCV for today's inference.
    """
    cfg = Config()
    cfg.tickers = ["AAPL"]
    model_mock, scaler_mock, features_mock = _build_inference_mocks()
    fetch_mock = MagicMock()
    fetch_mock.return_value = {"AAPL": _tiny_ohlcv()}

    invalidate_inference_cache()

    # side_effect with a list returns one element per call; supply four
    # entries to cover three date-1 calls and one date-2 call.
    dates = ["2025-01-15", "2025-01-15", "2025-01-15", "2025-01-16"]
    with (
        patch("src.inference.fetch_stock_data", fetch_mock),
        patch(
            "src.inference.compute_features_for_date",
            return_value=(features_mock, ["AAPL"]),
        ),
        patch(
            "src.inference.compute_market_state",
            return_value=np.zeros((1, 5), dtype=np.float32),
        ),
        patch("src.inference.load_scaler", return_value=scaler_mock),
        patch("src.inference.load_model", return_value=model_mock),
        patch("src.inference._last_business_day", side_effect=dates),
    ):
        run_inference(cfg)
        run_inference(cfg)
        run_inference(cfg)
        run_inference(cfg)

    assert fetch_mock.call_count == 2, (
        f"expected 1 fetch per date (2 total), got {fetch_mock.call_count}"
    )


def test_invalidate_inference_cache_drops_prior_data() -> None:
    """invalidate_inference_cache must clear prior entries.

    A re-call on a previously-cached date must trigger a fresh fetch.
    """
    cfg = Config()
    cfg.tickers = ["AAPL"]
    model_mock, scaler_mock, features_mock = _build_inference_mocks()
    fetch_mock = MagicMock()
    fetch_mock.return_value = {"AAPL": _tiny_ohlcv()}

    invalidate_inference_cache()

    # Phase 1: two calls on different dates populate two cache entries.
    with (
        patch("src.inference.fetch_stock_data", fetch_mock),
        patch(
            "src.inference.compute_features_for_date",
            return_value=(features_mock, ["AAPL"]),
        ),
        patch(
            "src.inference.compute_market_state",
            return_value=np.zeros((1, 5), dtype=np.float32),
        ),
        patch("src.inference.load_scaler", return_value=scaler_mock),
        patch("src.inference.load_model", return_value=model_mock),
        patch(
            "src.inference._last_business_day",
            side_effect=["2025-01-15", "2025-01-16"],
        ),
    ):
        run_inference(cfg)
        run_inference(cfg)

    assert fetch_mock.call_count == 2

    invalidate_inference_cache()

    # Phase 2: date 2025-01-16 was cached before, but the cache is cleared,
    # so this call must fetch again.
    with (
        patch("src.inference.fetch_stock_data", fetch_mock),
        patch(
            "src.inference.compute_features_for_date",
            return_value=(features_mock, ["AAPL"]),
        ),
        patch(
            "src.inference.compute_market_state",
            return_value=np.zeros((1, 5), dtype=np.float32),
        ),
        patch("src.inference.load_scaler", return_value=scaler_mock),
        patch("src.inference.load_model", return_value=model_mock),
        patch(
            "src.inference._last_business_day",
            side_effect=["2025-01-16"],
        ),
    ):
        run_inference(cfg)

    assert fetch_mock.call_count == 3, (
        f"invalidate must force refetch; got call_count={fetch_mock.call_count}"
    )
