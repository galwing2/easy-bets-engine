"""
prediction_worker.py
---------------------
Background worker that runs once per day, checks Polymarket for closed/resolved
markets, and updates the `predictions` collection with win/loss outcomes.

Run standalone:  python prediction_worker.py
Or via cron:     0 6 * * * /path/to/venv/bin/python /path/to/prediction_worker.py

Resolution logic:
  - Fetch the market from Polymarket Gamma API using the market_slug.
  - If the market is closed and a winner is clear (price ≥ 0.97 or ≤ 0.03),
    mark resolved = True and set won = True/False based on the verdict.
"""

import time
import requests
from datetime import datetime, timezone
from api.db import get_db
from config import POLYMARKET_GAMMA

GAMMA_SINGLE = "https://gamma-api.polymarket.com/markets?slug={slug}"
CHECK_DELAY  = 0.3   # seconds between API calls to avoid rate limits


def resolve_prediction(pred: dict, db) -> bool:
    """
    Checks Polymarket for the current price of the predicted market.
    Returns True if the prediction was successfully resolved.
    """
    slug = pred.get("market_slug")
    if not slug:
        return False

    try:
        resp = requests.get(GAMMA_SINGLE.format(slug=slug), timeout=8)
        if not resp.ok:
            return False
        markets = resp.json()
        if not markets:
            return False

        market = markets[0] if isinstance(markets, list) else markets

        # Parse outcome prices
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

        # Only resolve if the price has settled conclusively
        if yes_price >= 0.97:
            yes_won = True
        elif yes_price <= 0.03:
            yes_won = False
        else:
            return False  # Market still open / unresolved

        verdict = pred.get("verdict", "")
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

    print(f"  Resolved {resolved_count} predictions this pass.\n")


if __name__ == "__main__":
    print("Starting EasyBets prediction resolution worker (runs every 24h)...")
    while True:
        run_once()
        time.sleep(86400)  # 24 hours