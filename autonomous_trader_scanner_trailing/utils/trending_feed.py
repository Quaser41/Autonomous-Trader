# utils/trending_feed.py
import os, json, re, threading, time, requests
from typing import List, Set
from utils.market_data_cryptofeed import get_global_hub

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CFG_PATH = os.path.join(BASE_DIR, "config", "config.json")
RUNTIME_PATH = os.path.join(BASE_DIR, "data", "runtime", "runtime_whitelist.json")

# Try these quotes in order for each base; we’ll pick the first that exists in the feed.
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
    "USDT","USDC","USD"  # we validate actual pairs below
}

UA = {"User-Agent": "Mozilla/5.0 (compatible; TrendFetcher/1.2)"}

def save_whitelist(symbols: List[str]) -> None:
    os.makedirs(os.path.dirname(RUNTIME_PATH), exist_ok=True)
    with open(RUNTIME_PATH, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2)
    print(f"[TREND] Updated runtime whitelist with {len(symbols)} symbols.")

def _hub_symbols() -> Set[str]:
    hub = get_global_hub()
    if not hub:
        return set()
    return set(hub.list_symbols())  # slash style e.g. 'BTC/USDT'

def _configured_symbols() -> Set[str]:
    """Symbols from config.data_feeds.symbols, normalized to slash style."""
    try:
        cfg = json.load(open(CFG_PATH, "r", encoding="utf-8"))
        syms = (cfg.get("data_feeds", {}) or {}).get("symbols", []) or []
        out = set()
        for s in syms:
            s = (s or "").upper().replace("-", "/")
            if "/" not in s and s:
                s = f"{s}/USDT"
            out.add(s)
        return out
    except Exception:
        return set()

def _allowed_symbols() -> Set[str]:
    """Union of live hub symbols and configured symbols."""
    return _hub_symbols() | _configured_symbols()

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
            data = r.json()  # if HTML/blocked, raises; we ignore
            for pair in data.get("data", []):
                base = (pair.get("baseToken", {}) or {}).get("symbol")
                if base:
                    out.append(base.strip().upper())
        except Exception:
            # Silently ignore DEXTools failure (Cloudflare/HTML/blocked)
            pass
    return out

def fetch_reddit_mentions(limit=25) -> List[str]:
    """
    Prefer $TICKER; fallback to ALLCAPS tokens (2-6 chars).
    We'll validate against allowed symbols afterward to drop junk.
    """
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

def fetch_all_trending_validated() -> List[str]:
    """
    Merge CMC/DEXTools/Reddit, then for each base choose the first available
    quote among QUOTES (USDT/USDC/USD) that exists in the allowed universe.
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
            candidate = f"{base}/{quote}"
            if candidate in allow:
                valid_syms.append(candidate)
                break  # take first available quote and move on

    return valid_syms[:20]  # cap for safety

def trending_loop(interval_min=5):
    while True:
        try:
            syms = fetch_all_trending_validated()
            if syms:
                save_whitelist(syms)
            else:
                print("[TREND] No valid trending symbols yet (feed may be warming).")
        except Exception as e:
            print("[TREND] Loop error:", e)
        time.sleep(interval_min * 60)

def start_trending_feed(interval_min=5):
    t = threading.Thread(target=trending_loop, args=(interval_min,), daemon=True)
    t.start()
    print(f"[TREND] Trending feed started, refresh every {interval_min} min")
