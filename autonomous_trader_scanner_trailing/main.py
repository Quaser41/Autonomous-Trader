# utils/market_data_cryptofeed.py
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Deque, Optional, List, Tuple
import pandas as pd

from cryptofeed import FeedHandler
from cryptofeed.defines import TICKER, TRADES
from cryptofeed import exchanges as CFEX  # dynamic class lookup

# -------- module-level hub registry
_GLOBAL_HUB = None
def register_global_hub(hub):
    global _GLOBAL_HUB
    _GLOBAL_HUB = hub

def get_global_hub():
    return _GLOBAL_HUB

# -------- helpers

def norm_to_slash(sym: str) -> str:
    return sym.replace("-", "/")  # "BTC-USDT" -> "BTC/USDT"

def slash_to_norm(sym: str) -> str:
    return sym.replace("/", "-")  # "BTC/USDT" -> "BTC-USDT"

# Robust resolver for exchange classes across cryptofeed versions
_EX_CANDIDATES = {
    "BINANCE":  ["Binance"],
    "BITFINEX": ["Bitfinex"],
    "BYBIT":    ["Bybit", "BYBIT"],
    "OKX":      ["OKX", "Okx"],
    "KUCOIN":   ["Kucoin", "KuCoin"],
    "GATEIO":   ["Gateio", "GateIO"],
    "KRAKEN":   ["Kraken"],
}

def _resolve_exchange_class(name: str):
    upper = str(name).upper()
    for cand in _EX_CANDIDATES.get(upper, []):
        ex_cls = getattr(CFEX, cand, None)
        if ex_cls is not None:
            return ex_cls
    print(f"[FEED] Unknown exchange in config (no class found): {name}")
    return None

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

class CryptoFeedHub:
    """
    Maintains:
      - latest ticker per symbol
      - rolling trades & ATR estimator
      - simple OHLCV aggregation from trades (1m/5m/15m)
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
        # Normalize key to DASHED form so all lookups are consistent
        key = slash_to_norm(pair)  # e.g., "XBT/USDT" -> "XBT-USDT"
        price = None
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        else:
            price = kwargs.get('last', None)
        if price is None:
            return
        vol = kwargs.get('volume', None)
        self._ticker[key] = TickerSnapshot(
            price=float(price),
            volume_24h=(float(vol) if vol is not None else None),
            ts=float(timestamp),
        )
        if not self._printed_ready:
            print(f"[FEED] First ticker received for {pair}")
            self._printed_ready = True
        self._ready_evt.set()

    async def _on_trade(self, feed, pair, order_id, timestamp, side, amount, price, receipt_timestamp, **kwargs):
        key = slash_to_norm(pair)  # store trades under the same dashed key
        tp = TradePrint(price=float(price), size=float(amount), ts=float(timestamp))
        dq = self._trades[key]
        dq.append(tp)
        self._atr[key].on_trade(tp.ts, tp.price)
        if key not in self._ticker:
            self._ready_evt.set()

    # ---------- public API

    def list_symbols(self) -> List[str]:
        # Report in slash form to user code
        syms = set(self._ticker.keys()) | set(self._trades.keys())
        return [norm_to_slash(s) for s in sorted(syms)]

    def snapshot(self, slash_symbol: str) -> Tuple[Optional[float], Optional[float]]:
        key = slash_to_norm(slash_symbol)
        tick = self._ticker.get(key)
        if not tick:
            return (None, None)
        return (tick.price, tick.volume_24h)

    def atr_pct(self, slash_symbol: str) -> Optional[float]:
        key = slash_to_norm(slash_symbol)
        atr = self._atr[key].atr
        tick = self._ticker.get(key)
        price = tick.price if tick else None
        if atr is None or price is None or price == 0:
            return None
        return (atr / price) * 100.0

    def ohlcv_df(self, slash_symbol: str, timeframe: str = "5m", limit: int = 200) -> Optional[pd.DataFrame]:
        key = slash_to_norm(slash_symbol)
        trades = list(self._trades.get(key, []))
        if not trades:
            return pd.DataFrame()

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
        df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume"])
        if limit and len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df

    async def wait_ready(self, timeout: float = 10.0):
        try:
            await asyncio.wait_for(self._ready_evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    # ---------- runner
    def run(self):
        df = self.cfg.get("data_feeds", {})
        exchanges = df.get("exchanges", [])
        channels_cfg = df.get("channels", [])
        symbols = df.get("symbols", [])

        want_ticker = "ticker" in [c.lower() for c in channels_cfg]
        want_trades = "trades" in [c.lower() for c in channels_cfg]
        chan_list = []
        if want_ticker: chan_list.append(TICKER)
        if want_trades: chan_list.append(TRADES)
        if not chan_list:
            print("[FEED] No channels requested; nothing to run.")
            return

        subs: Dict[type, Dict[str, List[str]]] = {}
        for ex in exchanges:
            ex_cls = _resolve_exchange_class(ex)
            if not ex_cls:
                continue
            subs[ex_cls] = {ch: symbols for ch in chan_list}
        if not subs:
            print("[FEED] No valid subscriptions assembled; nothing to run.")
            return

        self._fh = FeedHandler()
        for ex_cls, chans in subs.items():
            cbs = {}
            if TICKER in chans: cbs[TICKER] = self._on_ticker
            if TRADES in chans: cbs[TRADES] = self._on_trade
            try_syms = sorted({s for syms in chans.values() for s in syms})
            print(f"[FEED] Adding {ex_cls.__name__} with channels={list(chans.keys())} symbols={try_syms}")
            added = False
            try:
                self._fh.add_feed(ex_cls(subscribe=chans, callbacks=cbs))
                added = True
            except Exception as e:
                print(f"[FEED] {ex_cls.__name__} subscribe= path failed: {e.__class__.__name__}: {e}")
            if not added:
                try:
                    self._fh.add_feed(ex_cls(symbols=try_syms, channels=list(chans.keys()), callbacks=cbs))
                    added = True
                except Exception as e:
                    print(f"[FEED] {ex_cls.__name__} symbols/channels path failed: {e.__class__.__name__}: {e}")
            if added:
                print(f"[FEED] Added {ex_cls.__name__}")
            else:
                print(f"[FEED] Skipping {ex_cls.__name__} due to startup errors.")

        print("[FEED] Starting FeedHandler...")

        # Create and own an event loop in main thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._fh.run(start_loop=False)
            loop.run_forever()
        except KeyboardInterrupt:
            print("\n[FEED] Ctrl+C received. Shutting down feed...")
        except Exception as e:
            print("[FEED] FeedHandler exited with error:", e)
        finally:
            try:
                for task in asyncio.all_tasks(loop):
                    task.cancel()
            except Exception:
                pass
            try:
                loop.stop()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
