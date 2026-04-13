"""
prediction_worker.py
---------------------
Background worker that runs every 12 hours, checks Polymarket for CLOSED
markets, and updates the predictions collection with win/loss outcomes.

Resolution rules (BOTH must be true):
  1. Market must be closed: closed=True AND active=False from the Gamma API.
  2. Price must have settled: YES >= 0.97 or YES <= 0.03.

A live market trading at 95c is NOT resolved — only closed + settled markets are.

Run standalone:  python prediction_worker.py
"""

import json
import time
import requests
from datetime import datetime, timezone
from api.db import get_db

GAMMA_SINGLE = "https://gamma-api.polymarket.com/markets?slug={slug}"
CHECK_DELAY  = 0.3


def resolve_prediction(pred: dict, db) -> bool:
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

        # ── GATE 1: market must actually be closed ────────────────────────────
        # Polymarket sets closed=True and active=False when trading has ended.
        # A market at 95c YES that is still ACTIVE is not resolved — skip it.
        is_closed = market.get("closed", False)
        is_active = market.get("active", True)

        if not is_closed and is_active:
            return False  # still live, do nothing

        # ── GATE 2: price must have settled conclusively ──────────────────────
        outcomes = market.get("outcomes", [])
        prices   = market.get("outcomePrices", [])

        if isinstance(outcomes, str):
            try:    outcomes = json.loads(outcomes)
            except: return False
        if isinstance(prices, str):
            try:    prices = json.loads(prices)
            except: return False

        if "Yes" not in outcomes or len(prices) != 2:
            return False

        yi        = outcomes.index("Yes")
        yes_price = float(prices[yi])

        if yes_price >= 0.97:
            yes_won = True
        elif yes_price <= 0.03:
            yes_won = False
        else:
            # Closed but mid-resolution (price not yet at 0/1) — wait for next pass
            print("  SKIP (closed but unsettled at {:.2f}): {}".format(yes_price, slug))
            return False

        # ── Determine win/loss ────────────────────────────────────────────────
        verdict = pred.get("verdict") or pred.get("ai_verdict", "")
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
        print("  {}  {}".format("WON" if won else "LOST", pred.get("question", slug)[:60]))
        return True

    except Exception as e:
        print("  Error resolving {}: {}".format(slug, e))
        return False


def run_once():
    print("\n[{}] Starting resolution pass...".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    db = get_db()

    unresolved = list(db["predictions"].find({"resolved": False}))
    print("  Found {} unresolved predictions.".format(len(unresolved)))

    resolved_count = 0
    for pred in unresolved:
        if resolve_prediction(pred, db):
            resolved_count += 1
        time.sleep(CHECK_DELAY)

    print("  Resolved {} this pass.\n".format(resolved_count))


if __name__ == "__main__":
    print("Starting EasyBets prediction resolution worker (runs every 12h)...")
    while True:
        run_once()
        time.sleep(43200)