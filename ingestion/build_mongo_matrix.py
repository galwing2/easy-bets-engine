"""
ingestion/build_mongo_matrix.py
--------------------------------
Fetches ALL closed binary markets from Polymarket (paginated),
pulls price histories, and stores training rows in MongoDB.

Run:  python -m ingestion.build_mongo_matrix
      python -m ingestion.build_mongo_matrix --limit 500   # quick test
      python -m ingestion.build_mongo_matrix --fresh        # drop + rebuild
"""

import sys
import json
import time
import argparse

import requests
from pymongo import MongoClient

# Allow running as a module from the project root
sys.path.insert(0, ".")
from config import MONGO_URI, MONGO_DB

client = MongoClient(MONGO_URI)
db     = client[MONGO_DB]

# ── Config ─────────────────────────────────────────────────────────────────────
GAMMA_BASE      = "https://gamma-api.polymarket.com"
CLOB_BASE       = "https://clob.polymarket.com"
PAGE_SIZE       = 100
REQUEST_DELAY   = 0.15   # seconds between requests
HISTORY_TIMEOUT = 6


def safe_parse(val) -> list:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val if isinstance(val, list) else []


def fetch_events_page(offset: int) -> list:
    url = (
        f"{GAMMA_BASE}/events"
        f"?closed=true&limit={PAGE_SIZE}&offset={offset}"
        f"&order=volume&ascending=false"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"   ⚠️  Page fetch failed at offset {offset}: {e}")
        return []


def fetch_price_history(token_id: str) -> list:
    for params in ["interval=max", "fidelity=1440"]:
        url = f"{CLOB_BASE}/prices-history?market={token_id}&{params}"
        try:
            r       = requests.get(url, timeout=HISTORY_TIMEOUT)
            history = r.json().get("history", [])
            if history:
                return history
        except Exception:
            pass
    return []


def process_market(market: dict) -> list:
    outcomes = safe_parse(market.get("outcomes", []))
    prices   = safe_parse(market.get("outcomePrices", []))
    clob_ids = safe_parse(market.get("clobTokenIds", []))

    if "Yes" not in outcomes or "No" not in outcomes:
        return []
    try:
        prices_float = [float(p) for p in prices]
    except Exception:
        return []
    if len(prices_float) != 2:
        return []

    yes_idx = outcomes.index("Yes")
    no_idx  = outcomes.index("No")

    if prices_float[yes_idx] >= 0.95 and prices_float[no_idx] <= 0.05:
        target = 1
    elif prices_float[yes_idx] <= 0.05 and prices_float[no_idx] >= 0.95:
        target = 0
    else:
        return []

    try:
        yes_token_id = clob_ids[yes_idx]
    except (IndexError, TypeError):
        return []

    history = fetch_price_history(str(yes_token_id))
    if not history:
        return []

    return [
        {
            "market_id": market.get("id"),
            "question":  market.get("question", ""),
            "timestamp": point["t"],
            "yes_price": float(point["p"]),
            "target":    target,
        }
        for point in history
    ]


def build_dataset(max_markets: int | None = None):
    print("=" * 60)
    print("  Polymarket Training Matrix Builder")
    print("=" * 60)

    all_rows     = []
    markets_seen = 0
    markets_used = 0
    offset       = 0
    empty_pages  = 0

    while True:
        if max_markets and markets_seen >= max_markets:
            print(f"\n   Reached --limit {max_markets}, stopping.")
            break

        print(f"\n📄 Fetching page offset={offset}...")
        events = fetch_events_page(offset)

        if not events:
            empty_pages += 1
            if empty_pages >= 3:
                print("   3 empty pages in a row — end of data.")
                break
            offset += PAGE_SIZE
            continue

        empty_pages = 0

        for event in events:
            for market in event.get("markets", []):
                if max_markets and markets_seen >= max_markets:
                    break
                markets_seen += 1
                rows = process_market(market)
                if rows:
                    all_rows.extend(rows)
                    markets_used += 1
                time.sleep(REQUEST_DELAY)

        print(
            f"   Page done. Seen: {markets_seen} | "
            f"Usable: {markets_used} | Rows: {len(all_rows):,}"
        )
        offset += PAGE_SIZE

        # Checkpoint every 500 markets
        if markets_seen % 500 == 0 and all_rows:
            _save(all_rows, label="[checkpoint]")
            all_rows = []

    if all_rows:
        _save(all_rows, label="[final]")

    total = db["training_matrix"].count_documents({})
    print("\n" + "=" * 60)
    print(f"  ✅ Done! Markets processed: {markets_seen} | Usable: {markets_used}")
    print(f"     Total rows in DB: {total:,}")
    print("=" * 60)


def _save(rows: list, label: str = ""):
    if not rows:
        return
    db["training_matrix"].insert_many(rows)
    print(f"   💾 Saved {len(rows):,} rows {label}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Max markets to process (omit for ALL)")
    parser.add_argument("--fresh", action="store_true",
                        help="Drop existing training_matrix before starting")
    args = parser.parse_args()

    if args.fresh:
        print("🗑️  Dropping existing training_matrix...")
        db["training_matrix"].drop()

    build_dataset(max_markets=args.limit)
