"""
prediction_worker.py
---------------------
Background worker that runs every 1 hour, checks Polymarket for closed/resolved
markets, and updates the `predictions` collection with win/loss outcomes.

Resolution logic:
  - ONLY attempt to resolve if the market's official endDate has passed.
  - If a winner is clear (price ≥ 0.97 or ≤ 0.03), mark resolved = True.
  - If Polymarket has completely deleted the market, auto-remove it from the DB.
"""

import time
import requests
from datetime import datetime, timezone
from api.db import get_db

GAMMA_SINGLE = "https://gamma-api.polymarket.com/markets?slug={slug}"
CHECK_DELAY  = 0.3   # seconds between API calls to avoid rate limits


def resolve_prediction(pred: dict, db) -> bool:
    slug = pred.get("market_slug")
    if not slug:
        # It's an old market without a slug, auto-clean it.
        db["predictions"].delete_one({"cache_key": pred["cache_key"]})
        return False

    try:
        resp = requests.get(GAMMA_SINGLE.format(slug=slug), timeout=8)
        if not resp.ok:
            return False
            
        markets = resp.json()
        
        # ── THE AUTO-GHOST PROTOCOL ──────────────────────────────────────
        # If Polymarket returns an empty list, the market was deleted/voided.
        if not markets:
            print(f"  👻 GHOST MARKET: {slug} was deleted by Polymarket. Removing from DB.")
            db["predictions"].delete_one({"cache_key": pred["cache_key"]})
            return False
        # ─────────────────────────────────────────────────────────────────

        market = markets[0] if isinstance(markets, list) else markets

        # Check if the end date has passed
        end_date_str = market.get("endDate")
        if end_date_str:
            try:
                clean_date_str = end_date_str.replace("Z", "+00:00")
                end_date = datetime.fromisoformat(clean_date_str)
                if datetime.now(timezone.utc) < end_date:
                    return False  # Still in the future
            except ValueError:
                pass 

        # Parse outcomes
        import json
        outcomes = market.get("outcomes", [])
        prices   = market.get("outcomePrices", [])

        if isinstance(outcomes, str):
            try: outcomes = json.loads(outcomes)
            except: return False
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: return False

        if "Yes" not in outcomes or len(prices) != 2:
            return False

        yi = outcomes.index("Yes")
        yes_price = float(prices[yi])

        # Check for settled prices
        if yes_price >= 0.97:
            yes_won = True
        elif yes_price <= 0.03:
            yes_won = False
        else:
            return False  # Unresolved mathematically

        verdict = pred.get("verdict", "")
        if not verdict:
            verdict = pred.get("ai_verdict", "")
            
        if verdict == "BUY_YES":
            won = yes_won
        elif verdict == "BUY_NO":
            won = not yes_won
        else:
            return False

        db["predictions"].update_one(
            {"cache_key": pred["cache_key"]},
            {"$set": {
                "resolved":      True,
                "won":           won,
                "resolve_price": yes_price,
                "resolved_at":   datetime.now(timezone.utc).isoformat(),
            }}
        )
        result_str = "✅ WON" if won else "❌ LOST"
        print(f"  {result_str}  {pred.get('question', slug)[:60]}")
        return True

    except Exception as e:
        print(f"  ⚠️  Error resolving {slug}: {e}")
        return False


def run_once():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting prediction resolution pass...")
    db = get_db()

    unresolved = list(db["predictions"].find({"resolved": False}))
    print(f"  Found {len(unresolved)} unresolved predictions.")

    resolved_count = 0
    for pred in unresolved:
        if resolve_prediction(pred, db):
            resolved_count += 1
        time.sleep(CHECK_DELAY)

    if resolved_count > 0:
        print(f"  Resolved {resolved_count} predictions this pass.\n")
    else:
        print("  No new resolutions this pass.\n")


if __name__ == "__main__":
    print("Starting EasyBets prediction resolution worker (runs every 1 hour)...")
    while True:
        run_once()
        time.sleep(3600)  # Changed from 12 hours to 1 hour