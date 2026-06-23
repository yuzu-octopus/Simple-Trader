import sys
from pathlib import Path

import pandas as pd
import tenacity
import yfinance as yf
from tqdm import tqdm


def fetch_stock_data(
    tickers: list[str],
    start: str,
    end: str,
    output_dir: str,
) -> dict[str, pd.DataFrame]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = {}
    cached = 0
    for ticker in tqdm(
        tickers, desc="Downloading stocks", unit="stock", file=sys.stderr
    ):
        path = out / f"{ticker}.csv"
        if path.exists():
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            cached += 1
        else:
            try:
                df = _download_with_retry(ticker, start, end)
            except Exception:
                tqdm.write(f"  Failed to download {ticker} after retries, skipping")
                continue
            df.to_csv(path)
        data[ticker] = df
    tqdm.write(f"  ({cached}/{len(tickers)} from cache)")
    return data


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    # wait_random_exponential adds jitter so concurrent agents don't thunder-herd
    # against yfinance; bounded max=10 keeps total wait <= 30s for 3 attempts.
    wait=tenacity.wait_random_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _download_with_retry(ticker: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # yfinance silently returns an empty frame for delisted/invalid tickers.
    # Re-raise inside the retry so tenacity actually retries; the outer
    # fetch_stock_data except logs and continues without this ticker.
    if df.empty:
        msg = f"yfinance returned empty data for {ticker}"
        raise ValueError(msg)
    return df
