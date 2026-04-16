"""
api/routes/markets.py — Polymarket data fetching, sports filtering, ML scoring.
"""
import json
import hashlib

import numpy as np
import requests
from fastapi import APIRouter, HTTPException

from config import POLYMARKET_GAMMA
from api.sports import is_sports_market, get_sport_category
from api.models import MarketRequest

router = APIRouter(prefix="/api", tags=["markets"])

# Shared ML model reference (loaded at startup in main.py)
_model = None

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
    events = []
    limit = 100  # Number of events to fetch per API page
    offset = 0
    
    # ── PAGINATION LOOP: Fetch ALL active events ────────────────────────
    while True:
        # Safely append parameters whether your config URL has a '?' or not
        sep = "&" if "?" in POLYMARKET_GAMMA else "?"
        url = f"{POLYMARKET_GAMMA}{sep}active=true&closed=false&limit={limit}&offset={offset}"
        
        try:
            resp = requests.get(url, timeout=10)
            if not resp.ok:
                break
            
            batch = resp.json()
            if not batch:  # Empty list means we reached the end
                break
                
            events.extend(batch)
            offset += limit
        except Exception as e:
            print(f"Pagination error at offset {offset}: {e}")
            break

    if not events:
        raise HTTPException(502, "Failed to fetch markets from Polymarket.")
    # ────────────────────────────────────────────────────────────────────

    out = []
    for event in events:
        title = event.get("title", "")
        tags  = event.get("tags") or []

        # Your custom sports filter acts as the bouncer!
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
    return {"markets": out}


@router.get("/stats")
def stats():
    events = []
    limit = 100
    offset = 0
    
    # ── PAGINATION LOOP FOR STATS ───────────────────────────────────────
    while True:
        sep = "&" if "?" in POLYMARKET_GAMMA else "?"
        url = f"{POLYMARKET_GAMMA}{sep}active=true&closed=false&limit={limit}&offset={offset}"
        try:
            resp = requests.get(url, timeout=5)
            if not resp.ok: break
            batch = resp.json()
            if not batch: break
            events.extend(batch)
            offset += limit
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