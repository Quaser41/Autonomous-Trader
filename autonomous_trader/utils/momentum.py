import pandas as pd


def apply_momentum_entry(df: pd.DataFrame, base_sig: dict, cfg: dict, debug: bool = False) -> dict:
    """Apply momentum-based entry override.

    Parameters
    ----------
    df : pandas.DataFrame
        OHLCV data with at least a 'close' column.
    base_sig : dict
        Signal dict from the main strategy.
    cfg : dict
        Global configuration; expects 'strategy.momentum_pct'.
    debug : bool, optional
        When True, prints momentum calculations for diagnostics.

    Returns
    -------
    dict
        Modified signal dict. If momentum threshold is exceeded, returns
        a BUY signal with momentum score; otherwise returns the original
        signal (with score updated to momentum value).
    """
    momentum_pct = (cfg.get("strategy") or {}).get("momentum_pct")
    if (
        base_sig.get("signal") == "HOLD"
        and momentum_pct is not None
        and momentum_pct > 0
        and len(df) >= 4
    ):
        c = df["close"]
        momo = (float(c.iloc[-1]) / float(c.iloc[-4])) - 1.0
        if debug:
            print(
                f"[MOMO] momentum={momo:.3%} (threshold={momentum_pct:.3%})"
            )
        if momo >= momentum_pct:
            return {"signal": "BUY", "score": momo}
        base_sig = dict(base_sig)
        base_sig["score"] = momo
        return base_sig
    return base_sig
