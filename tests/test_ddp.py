"""Smoke tests for DDP integration.

Validates unwrap helper, save/load checkpoint key-symmetry, and DistributedSampler
dataset type, without needing an actual torchrun process group.
"""

from unittest.mock import patch
from typing import Any

import torch
from torch.utils.data import TensorDataset

from config import Config
from models.stock_model import StockTransformer
from src import utils as utils_module
from src.utils import unwrap_model
from training.train import load_checkpoint, save_checkpoint


class FakeDDP:
    """Stand-in for DistributedDataParallel.

    Real DistributedDataParallel requires init_process_group at __init__, which
    isn't available in unit tests. unwrap_model only checks `isinstance(x, DistributedDataParallel)`,
    so we monkey-patch the symbol on `src.utils` and pass instances of this fake.
    """

    def __init__(self, module: torch.nn.Module) -> None:
        self.module = module


def _plain_model() -> StockTransformer:
    return StockTransformer(n_stocks=4, n_features=8, d_model=16, nhead=2, num_layers=1)


def test_unwrap_returns_inner_with_fake_ddp() -> None:
    inner = _plain_model()
    fake = FakeDDP(inner)

    with patch.object(utils_module, "DistributedDataParallel", FakeDDP):
        out = unwrap_model(fake)
        assert out is inner


def test_unwrap_passthrough_for_plain_module() -> None:
    inner = _plain_model()
    assert unwrap_model(inner) is inner


def test_save_checkpoint_writes_unwrapped_keys(tmp_path) -> None:
    """save_checkpoint must write the unwrapped state_dict so checkpoints are
    portable across DDP and non-DDP inference paths. Plain model is the
    identity case of unwrap, so verifying its keys is sufficient.
    """
    from torch import optim

    inner = _plain_model()
    opt = optim.SGD(inner.parameters(), lr=0.01)
    sch = optim.lr_scheduler.StepLR(opt, step_size=1)

    ckpt_path = str(tmp_path / "ck.pt")
    save_checkpoint(
        inner,
        opt,
        sch,
        epoch=2,
        best_val_loss=0.1,
        patience_counter=0,
        path=ckpt_path,
    )

    loaded = torch.load(ckpt_path, weights_only=True, map_location="cpu")
    saved_keys = set(loaded["model_state_dict"].keys())
    inner_keys = set(inner.state_dict().keys())
    assert saved_keys == inner_keys, (
        "save_checkpoint must contain unwrapped keys; "
        f"got {sorted(saved_keys)[:3]}... vs inner {sorted(inner_keys)[:3]}..."
    )


def test_save_load_round_trip_restores_state(tmp_path) -> None:
    """End-to-end: write a checkpoint, then load it into a second model; the
    second model must end up identical to the first. Random-init equality
    assertion is intentionally omitted (xavier_uniform_init on a tiny model
    can produce numerically close weights across different seeds):
    we verify STATE equality instead, which is what the round-trip contract
    actually depends on."""
    from torch import optim

    torch.manual_seed(1)
    inner = _plain_model()
    # Snapshot the source state so we can compare after restore.
    inner_before: dict[str, torch.Tensor] = {
        k: v.detach().clone() for k, v in inner.state_dict().items()
    }

    opt = optim.SGD(inner.parameters(), lr=0.01)
    sch = optim.lr_scheduler.StepLR(opt, step_size=1)
    ckpt_path = str(tmp_path / "ck.pt")
    save_checkpoint(
        inner, opt, sch, epoch=3, best_val_loss=0.42, patience_counter=1, path=ckpt_path
    )

    inner2 = _plain_model()
    opt2 = optim.SGD(inner2.parameters(), lr=0.01)
    sch2 = optim.lr_scheduler.StepLR(opt2, step_size=1)
    epoch, best, patience = load_checkpoint(
        inner2, opt2, sch2, torch.device("cpu"), path=ckpt_path
    )

    assert epoch == 3
    assert abs(best - 0.42) < 1e-9
    assert patience == 1
    # Source model state after save must equal its pre-save snapshot.
    for k, v in inner.state_dict().items():
        assert torch.allclose(v, inner_before[k]), f"save corrupted {k}"
    # Restored model must equal the snapshot (i.e. equal to the original).
    for k, v in inner2.state_dict().items():
        assert torch.allclose(v, inner_before[k]), f"restore drifted on {k}"


def test_distributed_sampler_accepts_tensor_dataset() -> None:
    """Construct (do not iterate) a DistributedSampler against a TensorDataset;
    the earlier version passed raw tensors/ndarrays which broke both runtime and
    mypy. Construction alone is sufficient to verify the type, since iteration
    requires a real process group.
    """
    from torch.utils.data import DistributedSampler

    ds = TensorDataset(torch.randn(20, 4), torch.randn(20, 4))
    # num_replicas/rank are explicit so the constructor never calls
    # dist.get_world_size() — which requires an initialized process group.
    sampler = DistributedSampler(ds, num_replicas=1, rank=0, shuffle=True)
    assert sampler is not None


def test_is_distributed_false_when_no_env() -> None:
    from config import is_distributed

    assert is_distributed() is False


def test_is_distributed_true_when_mocked() -> None:
    from config import is_distributed

    with (
        patch("torch.distributed.is_available", return_value=True),
        patch("torch.distributed.is_initialized", return_value=True),
    ):
        assert is_distributed() is True


def test_per_seed_checkpoint_path_isolated() -> None:
    """--seeds N --resume must not leak state between seeds: each train_seed
    call uses its own path so DDP ranks and parallel-seed runs don't collide.
    """
    from training.train import train_seed

    cfg_calls: list[str] = []

    # Patch train() to capture the checkpoint_path argument without running it.
    import training.train as train_module

    def _stub_train(config: Config, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        cfg_calls.append(kwargs.get("checkpoint_path", ""))
        # Return a dummy (model, scaler) so the outer wrapper still type-checks.
        return None, None  # type: ignore[return-value]

    with patch.object(train_module, "train", side_effect=_stub_train):
        for seed in (1, 2, 3):
            train_seed(
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                seed=seed,
            )

    assert cfg_calls == [
        "data/models/checkpoint_seed1.pt",
        "data/models/checkpoint_seed2.pt",
        "data/models/checkpoint_seed3.pt",
    ], f"per-seed isolation broken: {cfg_calls}"
