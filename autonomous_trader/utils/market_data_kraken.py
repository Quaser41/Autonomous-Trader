# utils/market_data_kraken.py
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Deque, Optional, List, Tuple
import pandas as pd

# We’ll use cryptofeed but *only* the Kraken exchange to keep it simple & stable
from cryptofeed import FeedHandler
from cryptofeed.defines import TICKER, TRADES
from cryptofeed.exchanges import Kraken

# -------- module-level hub registry (so other utils can access it)
_GLOBAL_HUB = None
def register_global_hub(hub):
    global _GLOBAL_HUB
    _GLOBAL_HUB = hub

def get_global_hub():
    return _GLOBAL_HUB

# -------- helpers
def norm_to_slash(sym: str) -> str:
    # "BTC-USDT" -> "BTC/USDT"
    return sym.replace("-", "/")

def slash_to_norm(sym: str) -> str:
    # "BTC/USDT" -> "BTC-USDT"
    return sym.replace("/", "-")

@dataclass
class TickerSnapshot:
    price: float
    volume_24h: Optional[float]
    ts: float

@dataclass
class TradePrint:
    price: float
    size: float
    ts: float

class AtrEstimator:
    """Very small ATR estimator on synthetic 1m bars from trades."""
    def __init__(self, minutes: int = 14):
        self.window = minutes
        self.curr_minute: Optional[int] = None
        self.ohlc: Optional[List[float]] = None  # [o, h, l, c]
        self.last_close: Optional[float] = None
        self.tr_values: Deque[float] = deque(maxlen=minutes)

    def on_trade(self, ts_sec: float, price: float):
        minute = int(ts_sec // 60)
        if self.curr_minute is None:
            self.curr_minute = minute
            self.ohlc = [price, price, price, price]
            return

        if minute != self.curr_minute:
            o, h, l, c = self.ohlc  # type: ignore
            prev_close = self.last_close if self.last_close is not None else o
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            self.tr_values.append(tr)
            self.last_close = c
            self.curr_minute = minute
            self.ohlc = [price, price, price, price]
        else:
            o, h, l, _ = self.ohlc  # type: ignore
            h = max(h, price)
            l = min(l, price)
            self.ohlc = [o, h, l, price]

    @property
    def atr(self) -> Optional[float]:
        if len(self.tr_values) < max(1, (self.tr_values.maxlen or 1) // 2):
            return None
        return sum(self.tr_values) / len(self.tr_values)

class KrakenHub:
    """
    Minimal Kraken-only live data hub.
    - Subscribes to ticker + trades for a provided symbol list
    - Maintains last price, 24h base volume (when available), rolling trades
    - Exposes snapshot(), atr_pct(), ohlcv_df(), list_symbols(), wait_ready()
    """
    def __init__(self, symbols: List[str]):
        # Input symbols can be "BTC-USDT" / "ETH-USDT" / ...
        # Cryptofeed’s Kraken adapter handles common aliasing (BTC<->XBT) for these pairs.
        self.symbols_norm = [slash_to_norm(s) if "/" in s else s for s in symbols]
        self._ticker: Dict[str, TickerSnapshot] = {}
        self._trades: Dict[str, Deque[TradePrint]] = defaultdict(lambda: deque(maxlen=10000))
        self._atr: Dict[str, AtrEstimator] = defaultdict(lambda: AtrEstimator(minutes=14))
        self._fh: Optional[FeedHandler] = None
        self._ready_evt = asyncio.Event()
        self._printed_any = False

    # -------- cryptofeed callbacks
    async def _on_ticker(self, feed, pair, bid, ask, timestamp, receipt_timestamp, **kwargs):
        # Prefer mid if bid/ask present; else try 'last'
        price = None
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        else:
            price = kwargs.get('last', None)
        if price is None:
            return
        vol = kwargs.get('volume', None)
        self._ticker[pair] = TickerSnapshot(
            price=float(price),
            volume_24h=(float(vol) if vol is not None else None),
            ts=float(timestamp),
        )
        if not self._printed_any:
            print(f"[KRAKEN] First update: {pair} price={price}")
            self._printed_any = True
        self._ready_evt.set()

    async def _on_trade(self, feed, pair, order_id, timestamp, side, amount, price, receipt_timestamp, **kwargs):
        tp = TradePrint(price=float(price), size=float(amount), ts=float(timestamp))
        dq = self._trades[pair]
        dq.append(tp)
        self._atr[pair].on_trade(tp.ts, tp.price)
        if pair not in self._ticker:
            self._ready_evt.set()

    # -------- public API
    def list_symbols(self) -> List[str]:
        # Use whatever we’ve actually seen so far
        syms = set(self._ticker.keys()) | set(self._trades.keys())
        return [norm_to_slash(s) for s in sorted(syms)]

    def snapshot(self, slash_symbol: str) -> Tuple[Optional[float], Optional[float]]:
        sym = slash_to_norm(slash_symbol)
        tick = self._ticker.get(sym)
        if not tick:
            return (None, None)
        return (tick.price, tick.volume_24h)

    def atr_pct(self, slash_symbol: str) -> Optional[float]:
        sym = slash_to_norm(slash_symbol)
        atr = self._atr[sym].atr
        price = self._ticker.get(sym).price if self._ticker.get(sym) else None
        if atr is None or price is None or price == 0:
            return None
        return (atr / price) * 100.0

    def ohlcv_df(self, slash_symbol: str, timeframe: str = "5m", limit: int = 200) -> pd.DataFrame:
        sym = slash_to_norm(slash_symbol)
        trades = list(self._trades.get(sym, []))
        if not trades:
            return pd.DataFrame()

        # Simple trade-to-bar aggregation for 1m/5m/15m
        tf_map = {"1m": 60, "5m": 300, "15m": 900}
        step = tf_map.get(timeframe, 300)
        buckets: Dict[int, List[float]] = {}
        for t in trades:
            b = int(t.ts // step) * step
            if b not in buckets:
                buckets[b] = [t.price, t.price, t.price, t.price, t.size]
            else:
                o, h, l, _, v = buckets[b]
                h = max(h, t.price)
                l = min(l, t.price)
                c = t.price
                v = v + t.size
                buckets[b] = [o, h, l, c, v]

        if not buckets:
            return pd.DataFrame()

        rows = [[b * 1000, *buckets[b]] for b in sorted(buckets.keys())]
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        if limit and len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df

    async def wait_ready(self, timeout: float = 10.0):
        try:
            await asyncio.wait_for(self._ready_evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    # -------- runner (to be called in the main thread)
    def run(self):
        """
        Start Kraken feed for the configured symbols. This call blocks and
        owns the event loop (so call it from your main thread).
        """
        if not self.symbols_norm:
            print("[KRAKEN] No symbols provided; nothing to run.")
            return

        self._fh = FeedHandler()

        chans = {
            TICKER: self.symbols_norm,
            TRADES: self.symbols_norm
        }
        cbs = {TICKER: self._on_ticker, TRADES: self._on_trade}

        print(f"[KRAKEN] Subscribing to {sorted(set(self.symbols_norm))}")
        self._fh.add_feed(Kraken(subscribe=chans, callbacks=cbs))

        # Let cryptofeed run/own its loop here (blocking)
        try:
            self._fh.run()
        except KeyboardInterrupt:
            print("\n[KRAKEN] Stopping feed…")
        except Exception as e:
            print("[KRAKEN] Feed run error:", e)
