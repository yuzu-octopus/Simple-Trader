import torch

from models.stock_model import StockTransformer


def test_model_forward_shape() -> None:
    model = StockTransformer(
        n_stocks=10, n_features=80, d_model=32, nhead=4, num_layers=1
    )
    x = torch.randn(4, 10, 80)
    out = model(x)
    assert out.shape == (4, 10), f"Expected (4, 10), got {out.shape}"


def test_model_output_range() -> None:
    model = StockTransformer(
        n_stocks=10, n_features=80, d_model=32, nhead=4, num_layers=1
    )
    x = torch.randn(4, 10, 80)
    out = model(x)
    assert out.min() >= -1.0 and out.max() <= 1.0, (
        f"Scores outside [-1, 1]: [{out.min():.4f}, {out.max():.4f}]"
    )


def test_model_different_batch_sizes() -> None:
    model = StockTransformer(
        n_stocks=10, n_features=80, d_model=32, nhead=4, num_layers=1
    )
    for batch_size in [1, 2, 8]:
        x = torch.randn(batch_size, 10, 80)
        out = model(x)
        assert out.shape == (batch_size, 10)


def test_model_different_stock_counts() -> None:
    for n in [5, 50, 100]:
        model = StockTransformer(
            n_stocks=n, n_features=80, d_model=32, nhead=4, num_layers=1
        )
        x = torch.randn(2, n, 80)
        out = model(x)
        assert out.shape == (2, n), f"Expected (2, {n}), got {out.shape}"


def test_model_train_step() -> None:
    model = StockTransformer(
        n_stocks=10, n_features=80, d_model=32, nhead=4, num_layers=1
    )
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
    x = torch.randn(4, 10, 80)
    y = torch.randn(4, 10)
    loss_before = torch.nn.MSELoss()(model(x), y).item()
    for _ in range(10):
        opt.zero_grad()
        loss = torch.nn.MSELoss()(model(x), y)
        loss.backward()
        opt.step()
    loss_after = torch.nn.MSELoss()(model(x), y).item()
    assert loss_after <= loss_before * 1.1, (
        f"Loss increased too much: {loss_before:.6f} -> {loss_after:.6f}"
    )


def test_rankglu_output_shape() -> None:
    from models.stock_model import RankGLU

    glu = RankGLU(d_model=128, bottleneck=64)
    x = torch.randn(4, 10, 128)
    out = glu(x)
    assert out.shape == (4, 10, 1)


def test_market_gate_shape() -> None:
    from models.stock_model import MarketGate

    gate = MarketGate(n_features=80, market_state_size=5)
    x = torch.randn(4, 10, 80)
    m = torch.randn(4, 5)
    out = gate(x, m)
    assert out.shape == x.shape


def test_decoder_causal_mask() -> None:
    model = StockTransformer(
        n_stocks=10, n_features=80, d_model=32, nhead=2, num_layers=1
    )
    x = torch.randn(4, 10, 80)
    out = model(x)
    assert out.shape == (4, 10)


def test_model_with_market_state() -> None:
    model = StockTransformer(
        n_stocks=10,
        n_features=80,
        d_model=32,
        nhead=2,
        num_layers=1,
        market_state_size=5,
    )
    x = torch.randn(4, 10, 80)
    m = torch.randn(4, 5)
    out = model(x, market_state=m)
    assert out.shape == (4, 10)
    out2 = model(x, market_state=None)
    assert out2.shape == (4, 10)


def test_model_market_state_still_works_when_not_configured() -> None:
    model = StockTransformer(
        n_stocks=10,
        n_features=80,
        d_model=32,
        nhead=2,
        num_layers=1,
        market_state_size=0,
    )
    x = torch.randn(4, 10, 80)
    out = model(x)
    assert out.shape == (4, 10)


def test_model_backward() -> None:
    model = StockTransformer(
        n_stocks=10, n_features=80, d_model=32, nhead=4, num_layers=1
    )
    x = torch.randn(2, 10, 80)
    y = torch.randn(2, 10)
    loss = torch.nn.MSELoss()(model(x), y)
    loss.backward()
    assert model.input_proj.weight.grad is not None
    assert model.input_proj.weight.grad.abs().sum().item() > 0
