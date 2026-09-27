"""
api/main.py
-----------
FastAPI backend for EasyBets — with two-layer caching.

Cache layers:
  1. Market cache     — raw Polymarket data, TTL 5 min (shared across all users)
  2. Scored cache     — edge-scored markets per category set, TTL 5 min
  3. Arb cache        — arb groups, TTL 5 min (same fetch as market cache)

Endpoints:
  GET  /                  → index.html
  GET  /api/stats         → live market counts
  GET  /api/cache-status  → cache health + age
  POST /api/markets       → personalized scored markets for a user profile

Run locally:  uvicorn api.main:app --reload --port 8000
On EC2:       uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

import os
import re
import json
import time
import asyncio
import threading
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────────
GAMMA_BASE      = "https://gamma-api.polymarket.com"
PAGE_SIZE       = 100
MARKET_TTL      = 300        # 5 min — how long raw market data is valid
SCORED_TTL      = 300        # 5 min — how long scored/categorised results are valid
FETCH_PAGES     = 5          # pages of open markets to fetch (100 markets each)
BASE_RATES_PATH = Path("models/base_rates.json")


# ── Cache store ────────────────────────────────────────────────────────────────
class Cache:
    """
    Thread-safe in-memory cache with TTL.
    Two named slots:
      "markets"  — raw list of market dicts from Polymarket
      "scored"   — dict keyed by frozenset(category_list) → scored markets + arb
    """

    def __init__(self):
        self._lock   = threading.Lock()
        self._store  = {}          # key → {"data": ..., "ts": float, "hits": int}

    def get(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, None   # (data, age_seconds)
            age = time.time() - entry["ts"]
            entry["hits"] += 1
            return entry["data"], age

    def set(self, key, data):
        with self._lock:
            self._store[key] = {"data": data, "ts": time.time(), "hits": 0}

    def is_fresh(self, key, ttl):
        _, age = self.get(key)
        return age is not None and age < ttl

    def stats(self):
        with self._lock:
            out = {}
            for k, v in self._store.items():
                out[k] = {
                    "age_seconds": round(time.time() - v["ts"], 1),
                    "hits":        v["hits"],
                    "size":        len(v["data"]) if isinstance(v["data"], list) else "dict",
                }
            return out

    def invalidate(self, key=None):
        with self._lock:
            if key:
                self._store.pop(key, None)
            else:
                self._store.clear()


CACHE = Cache()


# ── Base rates ─────────────────────────────────────────────────────────────────
def load_base_rates() -> dict:
    if BASE_RATES_PATH.exists():
        with open(BASE_RATES_PATH) as f:
            return json.load(f)
    return {
        "crypto_price":       {"yes_rate": 0.05, "n_markets": 11},
        "economic_threshold": {"yes_rate": 0.03, "n_markets": 22},
        "election_candidate": {"yes_rate": 0.04, "n_markets": 25},
        "ai_model":           {"yes_rate": 0.09, "n_markets": 11},
        "economic_rate":      {"yes_rate": 0.25, "n_markets": 4},
        "other":              {"yes_rate": 0.03, "n_markets": 88},
    }

BASE_RATES = load_base_rates()


# ── Category classifier ────────────────────────────────────────────────────────
CATEGORIES = [
    ("election_win",         [r"win.*election", r"elected", r"win.*primary"]),
    ("election_candidate",   [r"nominee", r"nominate", r"candidate"]),
    ("political_action",     [r"sign.*bill", r"pass.*law", r"veto", r"resign", r"impeach"]),
    ("political_appoint",    [r"appoint", r"nominate.*(?:secretary|judge|chair|director)"]),
    ("legal_verdict",        [r"convicted", r"acquitted", r"guilty", r"indicted", r"arrested"]),
    ("economic_rate",        [r"interest rate", r"fed.*rate", r"rate.*cut", r"rate.*hike"]),
    ("economic_threshold",   [r"gdp", r"inflation", r"recession", r"above \d", r"below \d",
                               r"reach \$", r"hit \$", r"exceed"]),
    ("crypto_price",         [r"bitcoin", r"ethereum", r"btc", r"eth", r"crypto", r"\$.*coin"]),
    ("sports_championship",  [r"championship", r"super bowl", r"world series", r"nba finals",
                               r"stanley cup", r"world cup", r"champions league"]),
    ("sports_win",           [r"win.*game", r"beat ", r"defeat", r"playoffs"]),
    ("sports_award",         [r"mvp", r"heisman", r"award", r"ballon"]),
    ("tech_product",         [r"release", r"launch", r"announce.*(?:product|model|version)"]),
    ("tech_company",         [r"ipo", r"acquisition", r"merger", r"bankrupt", r"layoff"]),
    ("ai_model",             [r"gpt", r"claude", r"gemini", r"llm", r"ai model"]),
    ("geopolitical_conflict",[r"war", r"ceasefire", r"invasion", r"military", r"sanction"]),
    ("deadline_by_date",     [r"by (?:january|february|march|april|may|june|july|august|"
                               r"september|october|november|december)",
                               r"before \d{4}", r"by end of", r"this year"]),
    ("other",                [r".*"]),
]

def classify(question: str) -> str:
    q = question.lower()
    for label, patterns in CATEGORIES:
        for pat in patterns:
            if re.search(pat, q):
                return label
    return "other"


# ── Interest → category mapping ────────────────────────────────────────────────
INTEREST_MAP = {
    "sports":           ["sports_win", "sports_championship", "sports_award"],
    "nba":              ["sports_win", "sports_championship", "sports_award"],
    "nfl":              ["sports_win", "sports_championship", "sports_award"],
    "mlb":              ["sports_win", "sports_championship", "sports_award"],
    "esports":          ["sports_win", "tech_product"],
    "crypto":           ["crypto_price", "economic_threshold"],
    "bitcoin":          ["crypto_price"],
    "stocks":           ["economic_threshold", "economic_rate", "tech_company"],
    "politics":         ["election_win", "election_candidate", "political_action", "political_appoint"],
    "ai":               ["ai_model", "tech_product", "tech_company"],
    "tech":             ["ai_model", "tech_product", "tech_company"],
    "entertainment":    ["other"],
    "world events":     ["geopolitical_conflict", "deadline_by_date"],
    "science":          ["other"],
    "champions league": ["sports_championship", "sports_win"],
    "trump":            ["political_action", "political_appoint", "election_candidate"],
    "elections":        ["election_win", "election_candidate"],
}

def interests_to_categories(interests: list) -> list:
    cats = set()
    for interest in interests:
        key = interest.lower().strip().replace("& ", "")
        for map_key, map_cats in INTEREST_MAP.items():
            if map_key in key or key in map_key:
                cats.update(map_cats)
    return list(cats) if cats else ["other"]


# ── Polymarket fetcher (called only on cache miss) ─────────────────────────────
def _safe_parse(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val if isinstance(val, list) else []

def _fetch_open_markets_from_api(pages: int = FETCH_PAGES) -> list:
    """Raw network fetch — do not call directly; use get_markets_cached()."""
    markets = []
    for page in range(pages):
        url = (f"{GAMMA_BASE}/events?closed=false&limit={PAGE_SIZE}"
               f"&offset={page * PAGE_SIZE}&order=volume&ascending=false")
        try:
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            events = r.json()
        except Exception as e:
            print(f"[fetch] Error on page {page}: {e}")
            break

        for event in events:
            for market in event.get("markets", []):
                outcomes = _safe_parse(market.get("outcomes", []))
                prices   = _safe_parse(market.get("outcomePrices", []))
                if "Yes" not in outcomes or "No" not in outcomes:
                    continue
                try:
                    prices_f = [float(p) for p in prices]
                except Exception:
                    continue
                if len(prices_f) != 2:
                    continue
                yes_idx   = outcomes.index("Yes")
                yes_price = prices_f[yes_idx]
                if yes_price >= 0.97 or yes_price <= 0.03:
                    continue
                markets.append({
                    "market_id": market.get("id"),
                    "question":  market.get("question", "").strip(),
                    "yes_price": yes_price,
                    "volume":    float(market.get("volume") or 0),
                    "event_id":  event.get("id"),
                    "category":  classify(market.get("question", "")),
                })
        time.sleep(0.05)

    return markets


# ── Cache layer 1: raw markets ─────────────────────────────────────────────────
def get_markets_cached() -> tuple[list, bool]:
    """
    Returns (markets, from_cache).
    Fetches from API only when cache is stale or empty.
    """
    if CACHE.is_fresh("markets", MARKET_TTL):
        data, _ = CACHE.get("markets")
        return data, True

    print(f"[cache] MISS markets — fetching from Polymarket API...")
    markets = _fetch_open_markets_from_api()
    CACHE.set("markets", markets)
    print(f"[cache] SET markets — {len(markets)} markets cached at "
          f"{datetime.now(timezone.utc).isoformat()}")
    return markets, False


# ── Cache layer 2: scored results per category set ─────────────────────────────
def get_scored_cached(markets: list, target_cats: list,
                      risk: str) -> tuple[list, list, bool]:
    """
    Returns (scored_markets, arb_groups, from_cache).
    Keyed by sorted category set + risk profile so different users with
    the same interests share a cached result.
    """
    cache_key = f"scored:{'|'.join(sorted(target_cats))}:{risk}"

    if CACHE.is_fresh(cache_key, SCORED_TTL):
        data, _ = CACHE.get(cache_key)
        return data["markets"], data["arb"], True

    print(f"[cache] MISS scored for key={cache_key}")
    scored_markets = _score_markets(markets, target_cats, risk)
    arb_groups     = _detect_arb_groups(markets)

    CACHE.set(cache_key, {"markets": scored_markets, "arb": arb_groups})
    return scored_markets, arb_groups, False


# ── Scoring logic ──────────────────────────────────────────────────────────────
def _score_markets(markets: list, target_cats: list, risk: str) -> list:
    # Filter to relevant categories
    relevant = [m for m in markets if m["category"] in target_cats]

    # Pad with high-volume markets if too few
    if len(relevant) < 10:
        seen_ids = {m["market_id"] for m in relevant}
        extra = [m for m in markets if m["market_id"] not in seen_ids]
        extra = sorted(extra, key=lambda x: x["volume"], reverse=True)[:20]
        relevant.extend(extra)

    # Score each
    scored = [_compute_edge(m) for m in relevant]

    # Filter by edge significance
    edgy = [m for m in scored if abs(m["edge"]) >= 0.08]

    # Risk profile filter
    r = risk.lower()
    if "safe" in r:
        edgy = [m for m in edgy if 0.35 <= m["yes_price"] <= 0.65]
    elif "long shot" in r:
        edgy = [m for m in edgy if m["yes_price"] < 0.25 or m["yes_price"] > 0.75]

    return sorted(edgy, key=lambda x: abs(x["edge"]), reverse=True)[:30]


def _compute_edge(market: dict) -> dict:
    cat  = market.get("category", "other")
    br   = BASE_RATES.get(cat, BASE_RATES.get("other", {"yes_rate": 0.5}))
    base = br["yes_rate"]
    edge = base - market["yes_price"]
    m    = dict(market)
    m["base_rate"]   = round(base, 4)
    m["edge"]        = round(edge, 4)
    m["signal_type"] = "edge"
    m["explanation"] = _generate_explanation(m, cat, base, edge)
    return m

def _generate_explanation(market: dict, cat: str, base_rate: float, edge: float) -> str:
    price_pct = int(market["yes_price"] * 100)
    base_pct  = int(base_rate * 100)
    if edge < -0.10:
        return (f"Historically, '{cat.replace('_', ' ')}' markets resolve YES only "
                f"~{base_pct}% of the time. This one is priced at {price_pct}¢ — "
                f"suggesting it's overpriced. Betting NO has edge.")
    elif edge > 0.10:
        return (f"Base rate for this market type is ~{base_pct}%. "
                f"At {price_pct}¢, YES looks underpriced. "
                f"The crowd may be underestimating this outcome.")
    elif abs(edge) < 0.05:
        return f"This market looks fairly priced relative to historical base rates ({base_pct}%)."
    direction = "slightly overpriced" if edge < 0 else "slight value on YES"
    return (f"Base rate: ~{base_pct}%. Current price: {price_pct}¢. "
            f"There's {direction} here.")


# ── Arb detection ──────────────────────────────────────────────────────────────
def _detect_arb_groups(markets: list, threshold: float = 0.05) -> list:
    by_event = defaultdict(list)
    for m in markets:
        if m.get("event_id"):
            by_event[m["event_id"]].append(m)

    opps = []
    for event_id, members in by_event.items():
        if len(members) < 2:
            continue
        total = sum(m["yes_price"] for m in members)
        edge  = total - 1.0
        if abs(edge) < threshold:
            continue
        fair = 1.0 / len(members)
        fade = [m for m in members if m["yes_price"] > fair * 1.1] if edge > 0 else []
        opps.append({
            "type":      "OVERPRICED" if edge > 0 else "UNDERPRICED",
            "total_yes": round(total, 4),
            "edge":      round(edge, 4),
            "action":    (
                f"Sum of YES prices is {int(total*100)}¢ — {int(abs(edge)*100)} cents "
                f"{'over' if edge > 0 else 'under'} fair value. "
                f"{'Fade the marked legs.' if edge > 0 else 'Buy all legs — one resolves YES.'}"
            ),
            "markets":   sorted(members, key=lambda x: x["yes_price"], reverse=True),
            "fade_legs": fade,
        })
    return sorted(opps, key=lambda x: abs(x["edge"]), reverse=True)[:5]


# ── Background refresh task ────────────────────────────────────────────────────
async def _background_refresh():
    """
    Silently refreshes the market cache every MARKET_TTL seconds.
    Runs as an asyncio background task so the first user never waits.
    """
    while True:
        await asyncio.sleep(MARKET_TTL)
        try:
            print("[cache] Background refresh triggered...")
            markets = await asyncio.to_thread(_fetch_open_markets_from_api)
            CACHE.set("markets", markets)
            # Invalidate scored caches so they rebuild on next request
            # (keeps arb detection fresh too)
            for key in list(CACHE._store.keys()):
                if key.startswith("scored:"):
                    CACHE.invalidate(key)
            print(f"[cache] Background refresh done — {len(markets)} markets.")
        except Exception as e:
            print(f"[cache] Background refresh failed: {e}")


# ── App lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm cache on startup so first request is instant
    print("[startup] Pre-warming market cache...")
    try:
        markets = await asyncio.to_thread(_fetch_open_markets_from_api)
        CACHE.set("markets", markets)
        print(f"[startup] Cache warm — {len(markets)} markets loaded.")
    except Exception as e:
        print(f"[startup] Pre-warm failed (will retry on first request): {e}")

    # Start background refresh loop
    task = asyncio.create_task(_background_refresh())

    yield   # app is running

    task.cancel()
    print("[shutdown] Background refresh cancelled.")


app = FastAPI(title="EasyBets API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = Path("index.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text())
    return HTMLResponse(content="<h1>EasyBets — index.html not found</h1>", status_code=404)


@app.get("/api/stats")
async def stats():
    markets, from_cache = get_markets_cached()
    arb_count = len(_detect_arb_groups(markets)) if markets else 0
    cache_data, age = CACHE.get("markets")
    return {
        "open_markets": len(markets),
        "arb_count":    arb_count,
        "cache_age_seconds": round(age, 1) if age else None,
        "from_cache":   from_cache,
        "status":       "ok",
    }


@app.get("/api/cache-status")
async def cache_status():
    """Inspect the health and age of all cache slots."""
    slots = CACHE.stats()
    now   = datetime.now(timezone.utc).isoformat()
    return {
        "timestamp":    now,
        "market_ttl":   MARKET_TTL,
        "scored_ttl":   SCORED_TTL,
        "slots":        slots,
        "total_slots":  len(slots),
        "market_fresh": CACHE.is_fresh("markets", MARKET_TTL),
    }


class Profile(BaseModel):
    interests:   list = []
    riskProfile: Optional[str] = None
    specific:    Optional[str] = None
    rawAnswers:  list = []

class MarketsRequest(BaseModel):
    profile: Profile


@app.post("/api/markets")
async def get_markets(req: MarketsRequest):
    profile = req.profile
    risk    = (profile.riskProfile or "").lower()

    # Layer 1: get raw markets (cached)
    all_markets, markets_from_cache = get_markets_cached()

    # Layer 2: get scored results (cached per category+risk combo)
    target_cats = interests_to_categories(profile.interests)
    scored, arb_groups, scored_from_cache = get_scored_cached(
        all_markets, target_cats, risk
    )

    return {
        "markets":           scored,
        "arb_groups":        arb_groups,
        "total_scanned":     len(all_markets),
        "profile_categories": target_cats,
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "cache": {
            "markets_from_cache": markets_from_cache,
            "scored_from_cache":  scored_from_cache,
        },
    }


@app.post("/api/cache/invalidate")
async def invalidate_cache():
    """Force a full cache flush — useful after retraining the model."""
    CACHE.invalidate()
    # Re-warm immediately
    markets = await asyncio.to_thread(_fetch_open_markets_from_api)
    CACHE.set("markets", markets)
    return {"status": "ok", "message": f"Cache cleared and re-warmed with {len(markets)} markets."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)