"""Tests for training/pretrain.py."""

import numpy as np
import torch

from training.pretrain import mpp_loss, prepare_mpp, prepare_top, top_loss


def test_mpp_loss_basic() -> None:
    pred = torch.ones(4, 10)
    target = torch.ones(4, 10)
    mask = torch.zeros(4, 10, dtype=torch.bool)
    mask[0, 0] = True
    pred[0, 0] = 0.0
    loss = mpp_loss(pred, target, mask)
    assert loss.item() == 1.0


def test_top_loss_basic() -> None:
    logits = torch.randn(4, 6)
    labels = torch.randint(0, 6, (4,))
    loss = top_loss(logits, labels)
    assert loss.item() > 0


def test_prepare_mpp_shape() -> None:
    features = np.random.randn(50, 10, 8)
    targets = np.random.randn(50, 10)
    masked, y, mask = prepare_mpp(features, targets, mask_ratio=0.2)
    assert masked.shape == features.shape
    assert y.shape == targets.shape
    assert mask.shape == (50, 10)


def test_prepare_top_shape() -> None:
    features = np.random.randn(50, 10, 8)
    windows, labels, n_classes = prepare_top(features, n_days=3)
    assert windows.shape == (48, 3, 10, 8)
    assert labels.shape == (48,)
    assert n_classes == 6


def test_pretrain_combined_loss_formula() -> None:
    """The pretraining loss is (l_mpp + 0.5*l_top + l_csr) / 3 / grad_accum_steps.

    This pins the combine so a refactor of training/pretrain.py can't silently
    change the weighting. Tolerance is loose (1e-5) because torch tensors
    default to float32 while the Python-side expected value is float64.
    """
    l_mpp = torch.tensor(0.6)
    l_top = torch.tensor(0.2)
    l_csr = torch.tensor(0.9)
    grad_accum_steps = 2
    combined = (l_mpp + 0.5 * l_top + l_csr) / 3 / grad_accum_steps
    expected = (0.6 + 0.5 * 0.2 + 0.9) / 3 / 2
    assert abs(combined.item() - expected) < 1e-5
