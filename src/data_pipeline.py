import sys
import time
from pathlib import Path

import pandas as pd
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


def _download_with_retry(
    ticker: str, start: str, end: str, attempts: int = 3
) -> pd.DataFrame:
    for attempt in range(attempts):
        try:
            df = yf.download(
                ticker, start=start, end=end, auto_adjust=True, progress=False
            )
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                msg = f"yfinance returned empty data for {ticker}"
                raise ValueError(msg)  # noqa: TRY301
            return df  # noqa: TRY300
        except Exception:
            if attempt < attempts - 1:
                time.sleep(2**attempt)
                continue
            raise
    return None
