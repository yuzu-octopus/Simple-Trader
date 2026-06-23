"""Tests for src/utils.py."""

from pathlib import Path

import torch

from config import Config
from models.stock_model import StockTransformer
from src.utils import create_model, load_threshold, unwrap_model


def test_create_model_returns_transformer() -> None:
    cfg = Config()
    cfg.tickers = ["AAPL", "MSFT"]
    model = create_model(cfg)
    inner = unwrap_model(model)
    assert isinstance(inner, StockTransformer)


def test_unwrap_model_plain_module() -> None:
    import torch

    inner = torch.nn.Linear(4, 4)
    assert unwrap_model(inner) is inner


def test_load_threshold_default_when_no_file(tmp_path: Path) -> None:
    cfg = Config()
    cfg.features_path = str(tmp_path)
    buy, sell = load_threshold(cfg)
    assert buy == 0.5
    assert sell == 0.5


def test_load_threshold_parses_file(tmp_path: Path) -> None:
    cfg = Config()
    cfg.features_path = str(tmp_path)
    (tmp_path / "threshold.txt").write_text("0.3,0.4")
    buy, sell = load_threshold(cfg)
    assert buy == 0.3
    assert sell == 0.4


def test_create_model_state_round_trip() -> None:
    """End-to-end: create -> unwrap -> save state -> re-create -> unwrap -> load.

    This exercises the realistic inference / checkpoint path and protects against
    regressions where create_model wraps in DDP and unwrap_model fails to peel it.
    """
    cfg1 = Config()
    cfg1.tickers = ["AAPL", "MSFT"]
    cfg1.d_model = 16
    cfg1.dim_feedforward = 32
    cfg1.num_layers = 1
    inner1 = unwrap_model(create_model(cfg1))
    snapshot = {k: v.detach().clone() for k, v in inner1.state_dict().items()}

    cfg2 = Config()
    cfg2.tickers = ["AAPL", "MSFT"]
    cfg2.d_model = 16
    cfg2.dim_feedforward = 32
    cfg2.num_layers = 1
    inner2 = unwrap_model(create_model(cfg2))
    inner2.load_state_dict(snapshot)

    for k, v in inner2.state_dict().items():
        assert torch.allclose(v, snapshot[k]), f"round-trip drifted on {k}"
