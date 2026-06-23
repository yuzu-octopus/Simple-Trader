import numpy as np
import torch

from config import Config
from src.data_pipeline import fetch_stock_data
from src.features import compute_features_for_date, compute_market_state
from src.utils import load_model, load_scaler


def run_inference(
    config: Config, buy_threshold: float = 0.5, sell_threshold: float = 0.5
) -> dict[str, dict]:
    raw_data = fetch_stock_data(
        config.tickers, config.train_start, config.test_end, config.raw_data_path
    )
    latest_date = str(sorted(raw_data[next(iter(raw_data))].index)[-1].date())
    features, tickers = compute_features_for_date(raw_data, latest_date)
    features = features[np.newaxis, :, :]

    market = compute_market_state(raw_data, [latest_date])

    scaler = load_scaler(f"{config.features_path}/scaler.json")
    scaled = scaler.transform(features.reshape(-1, config.n_features)).reshape(
        1, -1, config.n_features
    )

    model = load_model(config)
    device = next(model.parameters()).device
    with torch.no_grad():
        inp = torch.tensor(scaled, dtype=torch.float32).to(device)
        market_t = torch.tensor(market, dtype=torch.float32).to(device)
        scores = model(inp, market_state=market_t).cpu().numpy()[0]

    results = {}
    for i, ticker in enumerate(tickers):
        score = float(scores[i])
        if score > buy_threshold:
            signal = "BUY"
        elif score < -sell_threshold:
            signal = "SELL"
        else:
            signal = "HOLD"
        results[ticker] = {"score": score, "signal": signal}
    return results
