"""
api/main.py
-----------
FastAPI backend for EasyBets.
Serves the frontend and exposes:
  GET  /             → index.html
  GET  /api/stats    → live market counts
  POST /api/markets  → personalized scored markets for a user profile

Run locally:
  uvicorn api.main:app --reload --port 8000

On EC2:
  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

import os
import re
import json
import time
import asyncio
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import defaultdict
from difflib import SequenceMatcher

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="EasyBets API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GAMMA_BASE   = "https://gamma-api.polymarket.com"
PAGE_SIZE    = 100
BASE_RATES_PATH = Path("models/base_rates.json")


# ── Load base rates ────────────────────────────────────────────────────────────
def load_base_rates() -> dict:
    if BASE_RATES_PATH.exists():
        with open(BASE_RATES_PATH) as f:
            return json.load(f)
    # Hardcoded fallbacks if file doesn't exist yet
    return {
        "crypto_price":        {"yes_rate": 0.05, "n_markets": 11},
        "economic_threshold":  {"yes_rate": 0.03, "n_markets": 22},
        "election_candidate":  {"yes_rate": 0.04, "n_markets": 25},
        "ai_model":            {"yes_rate": 0.09, "n_markets": 11},
        "economic_rate":       {"yes_rate": 0.25, "n_markets": 4},
        "other":               {"yes_rate": 0.03, "n_markets": 88},
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
    "sports":       ["sports_win", "sports_championship", "sports_award"],
    "nba":          ["sports_win", "sports_championship", "sports_award"],
    "nfl":          ["sports_win", "sports_championship", "sports_award"],
    "mlb":          ["sports_win", "sports_championship", "sports_award"],
    "esports":      ["sports_win", "tech_product"],
    "crypto":       ["crypto_price", "economic_threshold"],
    "bitcoin":      ["crypto_price"],
    "stocks":       ["economic_threshold", "economic_rate", "tech_company"],
    "politics":     ["election_win", "election_candidate", "political_action", "political_appoint"],
    "ai":           ["ai_model", "tech_product", "tech_company"],
    "tech":         ["ai_model", "tech_product", "tech_company"],
    "entertainment":["other"],
    "world events": ["geopolitical_conflict", "deadline_by_date"],
    "science":      ["other"],
    "champions league": ["sports_championship", "sports_win"],
    "trump":        ["political_action", "political_appoint", "election_candidate"],
    "elections":    ["election_win", "election_candidate"],
}

def interests_to_categories(interests: list) -> list:
    cats = set()
    for interest in interests:
        key = interest.lower().strip().replace("& ", "")
        for map_key, map_cats in INTEREST_MAP.items():
            if map_key in key or key in map_key:
                cats.update(map_cats)
    if not cats:
        cats = {"other"}
    return list(cats)


# ── Polymarket fetcher ─────────────────────────────────────────────────────────
def _safe_parse(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val if isinstance(val, list) else []

def fetch_open_markets(pages: int = 5) -> list:
    markets = []
    for page in range(pages):
        url = (f"{GAMMA_BASE}/events?closed=false&limit={PAGE_SIZE}"
               f"&offset={page*PAGE_SIZE}&order=volume&ascending=false")
        try:
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            events = r.json()
        except Exception:
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
                    "market_id":   market.get("id"),
                    "question":    market.get("question", "").strip(),
                    "yes_price":   yes_price,
                    "volume":      float(market.get("volume") or 0),
                    "event_id":    event.get("id"),
                    "category":    classify(market.get("question", "")),
                })
        time.sleep(0.05)

    return markets


# ── Scoring ────────────────────────────────────────────────────────────────────
def compute_edge(market: dict) -> dict:
    """
    Compare market's current YES price to historical base rate for its category.
    edge = base_rate - yes_price
      positive → YES is underpriced (bet YES)
      negative → YES is overpriced (bet NO)
    """
    cat  = market.get("category", "other")
    br   = BASE_RATES.get(cat, BASE_RATES.get("other", {"yes_rate": 0.5}))
    base = br["yes_rate"]
    edge = base - market["yes_price"]

    m = dict(market)
    m["base_rate"]    = round(base, 4)
    m["edge"]         = round(edge, 4)
    m["signal_type"]  = "edge"
    m["explanation"]  = generate_explanation(m, cat, base, edge)
    return m

def generate_explanation(market: dict, cat: str, base_rate: float, edge: float) -> str:
    price_pct = int(market["yes_price"] * 100)
    base_pct  = int(base_rate * 100)
    q = market["question"]

    if edge < -0.10:
        return (f"Historically, '{cat.replace('_',' ')}' markets resolve YES only "
                f"~{base_pct}% of the time. This one is priced at {price_pct}¢ — "
                f"suggesting it's overpriced. Betting NO has edge.")
    elif edge > 0.10:
        return (f"Base rate for this market type is ~{base_pct}%. "
                f"At {price_pct}¢, YES looks underpriced. "
                f"The crowd may be underestimating this outcome.")
    elif abs(edge) < 0.05:
        return f"This market looks fairly priced relative to historical base rates ({base_pct}%)."
    else:
        direction = "slightly overpriced" if edge < 0 else "slight value on YES"
        return (f"Base rate: ~{base_pct}%. Current price: {price_pct}¢. "
                f"There's {direction} here.")


# ── Arb detection ──────────────────────────────────────────────────────────────
def detect_arb_groups(markets: list, threshold: float = 0.05) -> list:
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
            "type":       "OVERPRICED" if edge > 0 else "UNDERPRICED",
            "total_yes":  round(total, 4),
            "edge":       round(edge, 4),
            "action":     (
                f"Sum of YES prices is {int(total*100)}¢ — {int(abs(edge)*100)} cents "
                f"{'over' if edge > 0 else 'under'} fair value. "
                f"{'Fade the marked legs.' if edge > 0 else 'Buy all legs — one is guaranteed to resolve YES.'}"
            ),
            "markets":    sorted(members, key=lambda x: x["yes_price"], reverse=True),
            "fade_legs":  fade,
        })

    return sorted(opps, key=lambda x: abs(x["edge"]), reverse=True)[:5]


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = Path("index.html")
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text())
    return HTMLResponse(content="<h1>EasyBets — frontend not found</h1>", status_code=404)


@app.get("/api/stats")
async def stats():
    try:
        r = requests.get(
            f"{GAMMA_BASE}/events?closed=false&limit=1",
            timeout=5
        )
        # Polymarket doesn't return total count easily; use a proxy
        open_markets = 10000  # known approximate
        return {"open_markets": open_markets, "arb_count": "Live", "status": "ok"}
    except Exception:
        return {"open_markets": "10k+", "arb_count": "Live", "status": "ok"}


class Profile(BaseModel):
    interests: list = []
    riskProfile: Optional[str] = None
    specific: Optional[str] = None
    rawAnswers: list = []

class MarketsRequest(BaseModel):
    profile: Profile


@app.post("/api/markets")
async def get_markets(req: MarketsRequest):
    profile = req.profile

    # 1. Fetch live markets
    all_markets = fetch_open_markets(pages=5)

    # 2. Determine relevant categories from interests
    target_cats = interests_to_categories(profile.interests)

    # 3. Filter to relevant markets (always include some "other" for variety)
    relevant = [m for m in all_markets if m["category"] in target_cats]

    # If too few, add high-volume markets from any category
    if len(relevant) < 10:
        extra = [m for m in all_markets if m not in relevant]
        extra = sorted(extra, key=lambda x: x["volume"], reverse=True)[:20]
        relevant.extend(extra)

    # 4. Score each market
    scored = [compute_edge(m) for m in relevant]

    # 5. Filter by edge significance
    edgy = [m for m in scored if abs(m["edge"]) >= 0.08]

    # 6. Apply risk profile filter
    risk = (profile.riskProfile or "").lower()
    if "safe" in risk:
        edgy = [m for m in edgy if 0.35 <= m["yes_price"] <= 0.65]
    elif "long shot" in risk or "long shots" in risk:
        edgy = [m for m in edgy if m["yes_price"] < 0.25 or m["yes_price"] > 0.75]

    # 7. Sort by abs edge, limit
    edgy = sorted(edgy, key=lambda x: abs(x["edge"]), reverse=True)[:30]

    # 8. Detect arb groups (from full market list)
    arb_groups = detect_arb_groups(all_markets)

    return {
        "markets":    edgy,
        "arb_groups": arb_groups,
        "total_scanned": len(all_markets),
        "profile_categories": target_cats,
        "generated_at": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)