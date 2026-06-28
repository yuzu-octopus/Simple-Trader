"""Fetch crypto daily OHLCV data from Alpaca."""

import sys
from pathlib import Path

import pandas as pd
from alpaca.data import CryptoHistoricalDataClient
from alpaca.data.enums import CryptoFeed
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from tqdm import tqdm


def fetch_crypto_data(
    symbols: list[str],
    start: str,
    end: str,
    output_dir: str,
) -> dict[str, pd.DataFrame]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = {}
    cached = 0

    client = CryptoHistoricalDataClient()

    for symbol in tqdm(
        symbols, desc="Downloading crypto", unit="pair", file=sys.stderr
    ):
        safe_name = symbol.replace("/", "-")
        path = out / f"{safe_name}.csv"

        if path.exists():
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            cached += 1
        else:
            try:
                req = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    start=start,  # type: ignore[arg-type]
                    end=end,  # type: ignore[arg-type]
                    timeframe=TimeFrame.Day,
                )
                bars = client.get_crypto_bars(req, feed=CryptoFeed.US)
                records = []
                if symbol in bars.data:  # type: ignore[union-attr]
                    records = [
                        {
                            "Open": bar.open,
                            "High": bar.high,
                            "Low": bar.low,
                            "Close": bar.close,
                            "Volume": bar.volume,
                        }
                        for bar in bars.data[symbol]
                    ]
                if not records:
                    tqdm.write(f"  No data for {symbol}")
                    continue
                df = pd.DataFrame(records)
                df.index = pd.DatetimeIndex(
                    [b.timestamp for b in bars.data[symbol]]
                ).tz_localize(None)
                df.to_csv(path)
            except Exception as e:
                tqdm.write(f"  Failed to fetch {symbol}: {e}")
                continue

        data[symbol] = df

    tqdm.write(f"  ({cached}/{len(symbols)} from cache)")
    return data
