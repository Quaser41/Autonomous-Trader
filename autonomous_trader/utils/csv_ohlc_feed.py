from __future__ import annotations

from pathlib import Path
from typing import IO, Union

import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def read_csv_ohlcv(path: Union[str, Path, IO[str]]) -> pd.DataFrame:
    """Read OHLCV bars from a CSV file and validate integrity.

    Parameters
    ----------
    path:
        Path to a CSV file or an opened file-like object. The CSV must
        contain ``open``, ``high``, ``low``, ``close`` and ``volume``
        columns. Optional timestamp columns such as ``timestamp`` or
        ``date`` are used to ensure the data are ordered chronologically.

    Returns
    -------
    pandas.DataFrame
        DataFrame containing only the required OHLCV columns sorted in
        chronological order. The data types are coerced to ``float`` so the
        frame can be fed directly into
        :func:`strategies.ai_combo_strategy.generate_signal`.

    Raises
    ------
    ValueError
        If required columns are missing or if the data are not ordered
        chronologically.

    Examples
    --------
    Read a tiny CSV snippet and run the strategy.

    >>> import io
    >>> csv = io.StringIO("timestamp,open,high,low,close,volume\n0,1,2,0,1,10\n1,1,2,0,1,10")
    >>> df = read_csv_ohlcv(csv)
    >>> from strategies.ai_combo_strategy import generate_signal
    >>> cfg = {'strategy': {'buy_score_threshold': 0.0}}
    >>> generate_signal(df, cfg)['signal']
    'HOLD'
    """
    df = pd.read_csv(path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    time_col = next(
        (c for c in ["timestamp", "time", "date", "datetime"] if c in df.columns),
        None,
    )
    if time_col is not None:
        ts = pd.to_datetime(df[time_col])
        if not ts.is_monotonic_increasing:
            raise ValueError("CSV rows must be in chronological order")
        df = df.sort_values(time_col)

    return df[REQUIRED_COLUMNS].astype(float).reset_index(drop=True)
