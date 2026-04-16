"""
api/routes/markets.py — Polymarket data fetching, sports filtering, ML scoring.
"""
import json
import hashlib
import time

import numpy as np
import requests
from fastapi import APIRouter, HTTPException

from config import POLYMARKET_GAMMA
from api.sports import is_sports_market, get_sport_category
from api.models import MarketRequest

router = APIRouter(prefix="/api", tags=["markets"])

# Shared ML model reference (loaded at startup in main.py)
_model = None

# --- CACHING VARIABLES ---
_markets_cache = []
_last_fetch_time = 0
CACHE_TTL = 300  # 5 minutes
# -------------------------

def set_model(m):
    global _model
    _model = m

def _bucket(p: float) -> int:
    return 0 if p < .1 else 1 if p < .3 else 2 if p < .7 else 3 if p < .9 else 4

def _parse(v) -> list:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v if isinstance(v, list) else []

@router.post("/markets")
def markets(body: MarketRequest):
    global _markets_cache, _last_fetch_time

    # ── 1. The Shield: Return cached markets if less than 5 mins old ──
    if time.time() - _last_fetch_time < CACHE_TTL and _markets_cache:
        print("Returning cached sports markets!")
        return {"markets": _markets_cache}

    events = []
    limit = 500
    offset = 0
    max_pages = 3  # Grab up to 1,500 pure sports events
    pages_fetched = 0

    # ── 2. The Tunnel: Fetch DIRECTLY from Polymarket's Sports section ──
    while pages_fetched < max_pages:
        sep = "&" if "?" in POLYMARKET_GAMMA else "?"
        # Notice the `tag_slug=sports` parameter right here!
        url = f"{POLYMARKET_GAMMA}{sep}active=true&closed=false&tag_slug=sports&limit={limit}&offset={offset}"
        
        try:
            resp = requests.get(url, timeout=10)
            if not resp.ok:
                break
            batch = resp.json()
            if not batch: # Empty list means no more pages
                break
            events.extend(batch)
            offset += limit
            pages_fetched += 1
        except Exception as e:
            print(f"Error fetching sports markets: {e}")
            break

    if not events:
        if _markets_cache: return {"markets": _markets_cache}
        raise HTTPException(502, "Failed to fetch from Polymarket.")

    out = []
    for event in events:
        title = event.get("title", "")
        tags  = event.get("tags") or []

        # Keep as a backup bouncer in case Polymarket miscategorizes an event
        if not is_sports_market(title, tags):
            continue

        event_slug = event.get("slug", "")

        for m in event.get("markets", []):
            outcomes = _parse(m.get("outcomes", []))
            prices   = _parse(m.get("outcomePrices", []))
            
            if "Yes" not in outcomes or len(prices) != 2:
                continue
                
            try:
                pf = [float(x) for x in prices]
            except Exception:
                continue

            yi = outcomes.index("Yes")
            yp = pf[yi]
            
            if yp >= 0.97 or yp <= 0.03:
                continue

            market_slug  = m.get("slug", "")
            condition_id = m.get("conditionId", "")
            
            if event_slug and market_slug:
                poly_url = f"https://polymarket.com/event/{event_slug}/{market_slug}"
            elif event_slug:
                poly_url = f"https://polymarket.com/event/{event_slug}"
            else:
                poly_url = "https://polymarket.com"

            edge, ml = None, None
            if _model:
                feat = np.array([[yp, 0., 0., 0.05, float(_bucket(yp)), 0.5]])
                ml   = round(float(_model.predict_proba(feat)[0][1]), 4)
                edge = round(ml - yp, 4)

            question  = m.get("question", title)
            cache_key = hashlib.md5(question.encode()).hexdigest()

            out.append({
                "question":    question,
                "yes_price":   yp,
                "no_price":    pf[1 - yi],
                "volume":      float(m.get("volume", 0) or 0),
                "end_date":    (m.get("endDate") or "")[:10],
                "slug":        event_slug,
                "market_slug": market_slug,
                "condition_id":condition_id,
                "poly_url":    poly_url,
                "base_rate":   ml,
                "edge":        edge if edge is not None else (yp - 0.5),
                "signal_type": "edge" if (edge or 0) > 0 else "value",
                "category":    get_sport_category(title, tags),
                "cache_key":   cache_key,
            })

    out.sort(key=lambda x: abs(x.get("edge") or 0), reverse=True)
    
    # ── 3. Update Cache ──
    # Save the top 400 sorted edges to memory for the next 5 minutes
    _markets_cache = out[:400]
    _last_fetch_time = time.time()

    return {"markets": _markets_cache}

@router.get("/stats")
def stats():
    events = []
    limit = 500
    offset = 0
    max_pages = 2
    pages_fetched = 0
    
    while pages_fetched < max_pages:
        sep = "&" if "?" in POLYMARKET_GAMMA else "?"
        # We also add tag_slug=sports here to make the stats counter lightning fast
        url = f"{POLYMARKET_GAMMA}{sep}active=true&closed=false&tag_slug=sports&limit={limit}&offset={offset}"
        try:
            resp = requests.get(url, timeout=5)
            if not resp.ok: break
            batch = resp.json()
            if not batch: break
            events.extend(batch)
            offset += limit
            pages_fetched += 1
        except Exception:
            break
            
    if not events:
        return {"open_markets": "500+"}

    count = sum(
        len(e.get("markets", []))
        for e in events
        if is_sports_market(e.get("title", ""), e.get("tags") or [])
    )
    return {"open_markets": count}