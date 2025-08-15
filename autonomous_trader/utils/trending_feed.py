# utils/trending_feed.py
import os, json, re, threading, time, requests
from typing import List, Set, Optional

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CFG_PATH = os.path.join(BASE_DIR, "config", "config.json")
RUNTIME_PATH = os.path.join(BASE_DIR, "data", "runtime", "runtime_whitelist.json")

# Try these quotes in order for each base; we’ll pick the first that exists in the feed/markets.
QUOTES = ["USDT", "USDC", "USD"]

# Sources
REDDIT_SUBS = ["CryptoCurrency", "CryptoMarkets", "SatoshiStreetBets", "Altcoin"]
COINMARKETCAP_TRENDING_URL = "https://api.coinmarketcap.com/data-api/v3/topsearch/rank"
DEXTOOLS_TRENDING_URLS = [
    "https://www.dextools.io/shared/data/pairs/trending?chain=ether",
    "https://www.dextools.io/shared/data/pairs/trending?chain=bsc",
]

# Stopwords to avoid false positives from Reddit ALLCAPS scan
STOPWORDS = {
    "A","AN","AND","THE","FOR","WITH","TO","OF","ON","IN","IS","ARE","ALL","HERE","READ","RULES","THIS",
    "USDT","USDC","USD"  # validated later anyway
}

UA = {"User-Agent": "Mozilla/5.0 (compatible; TrendFetcher/1.2)"}

# --- Runtime whitelist merge/update helpers ---
_update_lock = threading.Lock()

def update_runtime_whitelist(new_syms: List[str], max_symbols: Optional[int] = None) -> List[str]:
    """Merge ``new_syms`` with any existing runtime whitelist and persist.

    New symbols take precedence over existing ones. The final list is
    deduplicated and capped by ``max_symbols`` (defaults to the scanner
    ``max_symbols`` config value or 20).
    """
    if max_symbols is None:
        cfg = _load_cfg()
        max_symbols = int(((cfg.get("scanner") or {}).get("max_symbols", 20)))

    os.makedirs(os.path.dirname(RUNTIME_PATH), exist_ok=True)
    with _update_lock:
        existing: List[str] = []
        if os.path.exists(RUNTIME_PATH):
            try:
                with open(RUNTIME_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f) or []
            except Exception:
                existing = []

        merged: List[str] = []
        seen = set()
        for sym in new_syms + existing:
            s = sym.strip().upper()
            if s and s not in seen:
                merged.append(s)
                seen.add(s)
            if len(merged) >= max_symbols:
                break

        tmp_path = RUNTIME_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
        os.replace(tmp_path, RUNTIME_PATH)

    print(f"[TREND] Updated runtime whitelist with {len(merged)} symbols.")
    return merged


def save_whitelist(symbols: List[str]) -> None:
    update_runtime_whitelist(symbols)

def _load_cfg():
    try:
        return json.load(open(CFG_PATH, "r", encoding="utf-8"))
    except Exception:
        return {}

# --- resolve hub getter (works with Kraken-only or Cryptofeed hub)
def _get_hub():
    try:
        from utils.market_data_kraken import get_global_hub as _get
        hub = _get()
        if hub:
            return hub
    except Exception:
        pass
    try:
        from utils.market_data_cryptofeed import get_global_hub as _get
        return _get()
    except Exception:
        return None

def _hub_symbols() -> Set[str]:
    hub = _get_hub()
    if not hub:
        return set()
    try:
        return set(hub.list_symbols())  # slash style e.g. 'BTC/USDT'
    except Exception:
        return set()

def _configured_symbols() -> Set[str]:
    """Symbols from config.data_feeds.symbols, normalized to slash style."""
    cfg = _load_cfg()
    syms = (cfg.get("data_feeds", {}) or {}).get("symbols", []) or []
    out = set()
    for s in syms:
        s = (s or "").upper().replace("-", "/")
        if "/" not in s and s:
            s = f"{s}/USDT"
        out.add(s)
    return out

def _ccxt_market_symbols() -> Set[str]:
    """
    Load spot symbols from the configured exchange via CCXT (e.g., Kraken).
    We’ll only keep markets with quote in QUOTES.
    """
    try:
        cfg = _load_cfg()
        ex_name = (cfg.get("exchange") or "kraken").lower()
        import ccxt  # lazy import
        if not hasattr(ccxt, ex_name):
            return set()
        ex = getattr(ccxt, ex_name)({"enableRateLimit": True})
        markets = ex.load_markets()
        out = set()
        for sym, m in markets.items():
            try:
                if m.get("spot") and m.get("quote") in QUOTES:
                    out.add(sym.upper())
            except Exception:
                pass
        return out
    except Exception as e:
        print("[TREND] CCXT markets error:", e)
        return set()

def _allowed_symbols() -> Set[str]:
    """
    Allowed trading universe for validation = hub symbols ∪ configured symbols ∪ ccxt spot markets.
    This is what unlocks dynamic lists beyond the tiny config set.
    """
    allow = set()
    allow |= _hub_symbols()
    allow |= _configured_symbols()
    allow |= _ccxt_market_symbols()
    return allow

# ---- Sources

def fetch_cmc_trending() -> List[str]:
    out = []
    try:
        r = requests.get(COINMARKETCAP_TRENDING_URL, headers=UA, timeout=10)
        data = r.json()
        for item in data.get("data", {}).get("cryptoTopSearchRanks", []):
            sym = item.get("symbol")
            if sym:
                out.append(sym.strip().upper())
    except Exception as e:
        print("[TREND] CMC trending error:", e)
    return out

def fetch_dextools_trending() -> List[str]:
    out = []
    for url in DEXTOOLS_TRENDING_URLS:
        try:
            r = requests.get(url, headers=UA, timeout=10)
            data = r.json()
            for pair in data.get("data", []):
                base = (pair.get("baseToken", {}) or {}).get("symbol")
                if base:
                    out.append(base.strip().upper())
        except Exception:
            # often blocked by Cloudflare; skip silently
            pass
    return out

def fetch_reddit_mentions(limit=25) -> List[str]:
    out = []
    cash_pat = re.compile(r"\$([A-Za-z]{2,10})")
    caps_pat = re.compile(r"\b([A-Z]{2,10})\b")

    for sub in REDDIT_SUBS:
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
            r = requests.get(url, headers=UA, timeout=10)
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                data = post.get("data", {}) or {}
                text = (data.get("title","") + " " + data.get("selftext","")).upper()

                # First: $TICKER
                for m in cash_pat.findall(text):
                    tok = m.upper()
                    if tok not in STOPWORDS:
                        out.append(tok)

                # Fallback: ALLCAPS words
                for m in caps_pat.findall(text):
                    tok = m.upper()
                    if 2 <= len(tok) <= 6 and tok not in STOPWORDS and not tok.isdigit():
                        out.append(tok)
        except Exception as e:
            print(f"[TREND] Reddit error ({sub}):", e)
    return out

# ---- Merge, validate, write

def _alias_for_exchange(candidate: str) -> List[str]:
    """
    Handle common exchange-specific aliases (e.g., BTC <-> XBT on Kraken).
    Return a list of variants to try.
    """
    cfg = _load_cfg()
    ex = (cfg.get("exchange") or "").lower()
    cands = [candidate]
    if ex == "kraken":
        # If base = BTC, also try XBT
        if candidate.startswith("BTC/"):
            cands.append("XBT/" + candidate.split("/", 1)[1])
        # If base = XBT, also try BTC
        if candidate.startswith("XBT/"):
            cands.append("BTC/" + candidate.split("/", 1)[1])
    return cands

def fetch_all_trending_validated() -> List[str]:
    """
    Merge CMC/DEXTools/Reddit, then for each base choose the first available
    quote among QUOTES (USDT/USDC/USD) that exists in the allowed universe.
    Now backed by full CCXT market list, not just the tiny config list.
    """
    allow = _allowed_symbols()
    if not allow:
        return []

    # raw candidates (bases)
    bases: List[str] = []
    bases.extend(fetch_cmc_trending())
    bases.extend(fetch_dextools_trending())
    bases.extend(fetch_reddit_mentions())

    # dedupe bases preserving order
    seen_b = set()
    uniq_bases = []
    for b in bases:
        b = b.strip().upper()
        if not b or b in seen_b:
            continue
        seen_b.add(b)
        uniq_bases.append(b)

    # Validate: keep BASE/QUOTE if present in allowed symbol universe
    valid_syms: List[str] = []
    for base in uniq_bases:
        for quote in QUOTES:
            raw = f"{base}/{quote}"
            variants = _alias_for_exchange(raw)
            # choose first variant that exists in allowed markets
            pick = next((v for v in variants if v in allow), None)
            if pick:
                valid_syms.append(pick)
                break

    # cap for safety & stability
    return valid_syms[:20]

def trending_loop(interval_min=5):
    while True:
        try:
            syms = fetch_all_trending_validated()
            if syms:
                save_whitelist(syms)
            else:
                print("[TREND] No valid trending symbols yet (feed or markets may still be warming).")
        except Exception as e:
            print("[TREND] Loop error:", e)
        time.sleep(interval_min * 60)

def start_trending_feed(interval_min=5):
    t = threading.Thread(target=trending_loop, args=(interval_min,), daemon=True)
    t.start()
    print(f"[TREND] Trending feed started, refresh every {interval_min} min")
