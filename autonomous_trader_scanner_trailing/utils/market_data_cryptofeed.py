# utils/market_data_cryptofeed.py
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Deque, Optional, List, Tuple
import time
import pandas as pd

from cryptofeed import FeedHandler
from cryptofeed.defines import TICKER, TRADES
from cryptofeed.exchanges import EXCHANGE_MAP  # maps name -> class

# -------- module-level hub registry (for scanner & dummy exchange)
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
    volume_24h: Optional[float]  # base volume when available
    ts: float

@dataclass
class TradePrint:
    price: float
    size: float
    ts: float

# Simple ATR-ish calc from synthetic 1m bars composed from trades
class AtrEstimator:
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
            # close out previous bar
            o, h, l, c = self.ohlc  # type: ignore
            prev_close = self.last_close if self.last_close is not None else o
            tr = max(
                h - l,
                abs(h - prev_close),
                abs(l - prev_close),
            )
            self.tr_values.append(tr)
            self.last_close = c
            # start new bar
            self.curr_minute = minute
            self.ohlc = [price, price, price, price]
        else:
            # update bar
            o, h, l, _ = self.ohlc  # type: ignore
            h = max(h, price)
            l = min(l, price)
            self.ohlc = [o, h, l, price]

    @property
    def atr(self) -> Optional[float]:
        if len(self.tr_values) < max(1, (self.tr_values.maxlen or 1) // 2):
            return None
        return sum(self.tr_values) / len(self.tr_values)

class CryptoFeedHub:
    """
    Runs Cryptofeed FeedHandler and maintains:
      - latest ticker snapshot per symbol
      - rolling trade prints and ATR estimator
      - lightweight OHLCV aggregation from trades
    cfg example:
      {
        "data_feeds": {
          "exchanges": ["bybit","okx"],
          "channels":  ["ticker","trades"],
          "symbols":   ["BTC-USDT","ETH-USDT"]
        }
      }
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._ticker: Dict[str, TickerSnapshot] = {}
        self._trades: Dict[str, Deque[TradePrint]] = defaultdict(lambda: deque(maxlen=10000))
        self._atr: Dict[str, AtrEstimator] = defaultdict(lambda: AtrEstimator(minutes=14))
        self._fh: Optional[FeedHandler] = None
        self._ready_evt = asyncio.Event()
        self._printed_ready = False

    # ---------- callbacks

    async def _on_ticker(self, feed, pair, bid, ask, timestamp, receipt_timestamp, **kwargs):
        # price proxy from mid if possible, else from last
        price = None
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        else:
            price = kwargs.get('last', None)
        if price is None:
            return
        vol = kwargs.get('volume', None)  # base volume 24h when provided
        self._ticker[pair] = TickerSnapshot(
            price=float(price),
            volume_24h=(float(vol) if vol is not None else None),
            ts=float(timestamp),
        )
        if not self._printed_ready:
            print(f"[FEED] First ticker received for {pair}")
            self._printed_ready = True
        self._ready_evt.set()

    async def _on_trade(self, feed, pair, order_id, timestamp, side, amount, price, receipt_timestamp, **kwargs):
        tp = TradePrint(price=float(price), size=float(amount), ts=float(timestamp))
        dq = self._trades[pair]
        dq.append(tp)
        self._atr[pair].on_trade(tp.ts, tp.price)
        if pair not in self._ticker:
            self._ready_evt.set()

    # ---------- public API for scanner/strategy

    def list_symbols(self) -> List[str]:
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
        tick = self._ticker.get(sym)
        price = tick.price if tick else None
        if atr is None or price is None or price == 0:
            return None
        return (atr / price) * 100.0

    def ohlcv_df(self, slash_symbol: str, timeframe: str = "5m", limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Build simple OHLCV from trades in memory. Supports '1m','5m','15m'.
        If there are not enough trades yet, returns an empty DataFrame.
        """
        sym = slash_to_norm(slash_symbol)
        trades = list(self._trades.get(sym, []))
        if not trades:
            return pd.DataFrame()

        # choose bucket size
        tf_map = {"1m": 60, "5m": 300, "15m": 900}
        step = tf_map.get(timeframe, 300)

        # build rows: [(bucket_ts_ms, o,h,l,c,v)]
        # use Unix seconds from trades, convert to ms for ccxt-like schema
        buckets: Dict[int, List[float]] = {}  # ts -> [o,h,l,c,v]
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

        rows = []
        for b in sorted(buckets.keys()):
            o, h, l, c, v = buckets[b]
            rows.append([b * 1000, o, h, l, c, v])

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume"])
        if limit and len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df

    async def wait_ready(self, timeout: float = 10.0):
        """Wait until at least one update (ticker or trade) has been received."""
        try:
            await asyncio.wait_for(self._ready_evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    # ---------- runner

    async def run(self):
        df = self.cfg.get("data_feeds", {})
        exchanges = df.get("exchanges", [])
        channels = df.get("channels", [])
        symbols = df.get("symbols", [])

        # Build subscriptions per exchange using normalized symbols
        subs: Dict[object, Dict[str, List[str]]] = {}

        for ex in exchanges:
            ex_cls = EXCHANGE_MAP.get(str(ex).lower())
            if not ex_cls:
                continue

            want_ticker = "ticker" in [c.lower() for c in channels]
            want_trades = "trades" in [c.lower() for c in channels]

            chans: Dict[str, List[str]] = {}
            if want_ticker:
                chans[TICKER] = symbols
            if want_trades:
                chans[TRADES] = symbols
            if not chans:
                continue

            self._fh = self._fh or FeedHandler()
            self._fh.add_feed(
                ex_cls(
                    channels=chans,
                    callbacks={
                        **({TICKER: self._on_ticker} if TICKER in chans else {}),
                        **({TRADES: self._on_trade} if TRADES in chans else {}),
                    }
                )
            )

        if not self._fh:
            return  # nothing to run

        await self._fh.run(start_loop=False)
