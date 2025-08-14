"""Utilities for integrating live stock prices from Alpaca Markets.

This module fetches recent stock price bars from the Alpaca REST API,
normalizes them and provides helpers to feed the data into the existing
forecasting pipeline.

Examples
--------
The examples below demonstrate how the normalization works and how the
resulting data frame can be passed to the forecasting function.

>>> import pandas as pd
>>> raw = pd.DataFrame({'open':[10,11], 'high':[11,12], 'low':[9,10],
...                     'close':[10,12], 'volume':[100,150]})
>>> norm = _normalize_bars(raw)
>>> norm['close'].tolist()
[0.0, 1.0]
>>> format_for_forecast(norm).columns.tolist()
['open', 'high', 'low', 'close', 'volume']
>>> cfg = {'strategy': {'buy_score_threshold': 0.0}}
>>> generate_signal(format_for_forecast(norm), cfg)['signal']
'HOLD'
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

try:  # Lazy import so tests can run without the dependency installed.
    import alpacatradeapi as tradeapi
except Exception:  # pragma: no cover - handled at runtime
    tradeapi = None

from strategies.ai_combo_strategy import generate_signal


def fetch_and_normalize(
    symbol: str,
    api_key: str,
    secret_key: str,
    base_url: str = "https://paper-api.alpaca.markets",
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch recent bars for ``symbol`` and return normalized data.

    Parameters
    ----------
    symbol:
        Stock ticker, e.g. ``"AAPL"``.
    api_key:
        Alpaca API key.
    secret_key:
        Alpaca API secret.
    base_url:
        REST endpoint to use. Defaults to Alpaca's paper trading URL.
    limit:
        Number of 1-minute bars to request.

    Returns
    -------
    pandas.DataFrame
        DataFrame with normalized ``open``, ``high``, ``low`` and ``close``
        prices and original ``volume``.
    """
    if tradeapi is None:  # pragma: no cover - dependency missing
        raise ImportError("alpacatradeapi package is required to fetch data")

    api = tradeapi.REST(api_key, secret_key, base_url, api_version="v2")
    bars = api.get_bars(symbol, tradeapi.TimeFrame.Minute, limit=limit)
    df = bars.df[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    return _normalize_bars(df)


def _normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLC columns to the range [0, 1].

    The minimum and maximum of the ``close`` column are used as the
    reference scale for all OHLC prices.

    Parameters
    ----------
    df:
        Data frame containing raw OHLCV data.

    Returns
    -------
    pandas.DataFrame
        Normalized data frame.

    Examples
    --------
    >>> import pandas as pd
    >>> raw = pd.DataFrame({'open':[10,11], 'high':[11,12], 'low':[9,10],
    ...                     'close':[10,12], 'volume':[100,150]})
    >>> _normalize_bars(raw)[['open','high','low','close']].round(2).to_dict('list')
    {'open': [0.0, 0.5], 'high': [0.5, 1.0], 'low': [-0.5, 0.0], 'close': [0.0, 1.0]}
    """
    close_min = float(df['close'].min())
    close_max = float(df['close'].max())
    scale = close_max - close_min or 1.0

    price_cols = ['open', 'high', 'low', 'close']
    norm = df.copy()
    for col in price_cols:
        norm[col] = (df[col] - close_min) / scale
    return norm


def format_for_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """Format normalized bars for the forecasting function.

    The forecasting pipeline expects a data frame with ``open``, ``high``,
    ``low``, ``close`` and ``volume`` columns ordered chronologically.

    Examples
    --------
    >>> import pandas as pd
    >>> norm = pd.DataFrame({'open':[0.0,0.5], 'high':[0.5,1.0],
    ...                      'low':[-0.5,0.0], 'close':[0.0,1.0],
    ...                      'volume':[1,1]})
    >>> format_for_forecast(norm).columns.tolist()
    ['open', 'high', 'low', 'close', 'volume']
    >>> cfg = {'strategy': {'buy_score_threshold': 0.0}}
    >>> generate_signal(format_for_forecast(norm), cfg)['signal']
    'HOLD'
    """
    required = ['open', 'high', 'low', 'close', 'volume']
    return df[required].astype(float)


def forecast_from_alpaca(
    symbol: str,
    api_key: str,
    secret_key: str,
    cfg: Dict,
    **kwargs,
) -> Dict:
    """Fetch normalized data for ``symbol`` and generate a forecast.

    Parameters
    ----------
    symbol:
        Ticker symbol to request.
    api_key:
        Alpaca API key.
    secret_key:
        Alpaca API secret.
    cfg:
        Configuration dictionary forwarded to
        :func:`strategies.ai_combo_strategy.generate_signal`.
    **kwargs:
        Additional arguments forwarded to :func:`fetch_and_normalize`.

    Returns
    -------
    dict
        Signal dictionary as produced by
        :func:`strategies.ai_combo_strategy.generate_signal`.
    """
    df = fetch_and_normalize(symbol, api_key, secret_key, **kwargs)
    prepared = format_for_forecast(df)
    return generate_signal(prepared, cfg)
