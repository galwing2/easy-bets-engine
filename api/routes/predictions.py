"""
api/routes/predictions.py — AI prediction track record: save, update, and serve.
"""
from datetime import datetime, timezone
from fastapi import APIRouter
from api.db import get_db

# FIXED 1: Changed prefix to exactly match what app.js requests
router = APIRouter(prefix="/api/predictions", tags=["predictions"])

@router.post("/save")
def save_prediction(body: dict):
    """
    Called internally after a high/medium-confidence BUY verdict.
    Idempotent: upserts on cache_key so the same market isn't double-counted.
    """
    db = get_db()
    cache_key = body.get("cache_key")
    if not cache_key:
        return {"ok": False, "reason": "missing cache_key"}

    existing = db["predictions"].find_one({"cache_key": cache_key})
    if existing:
        return {"ok": True, "skipped": True}

    doc = {
        "cache_key":   cache_key,
        "question":    body.get("question", ""),
        "market_slug": body.get("market_slug", ""),
        "yes_price":   body.get("yes_price", body.get("entry_price")),
        "verdict":     body.get("verdict", body.get("ai_verdict")),         
        "fair_value":  body.get("fair_value"),
        "edge_pct":    body.get("edge_pct"),
        "confidence":  body.get("confidence"),
        "end_date":    body.get("end_date", ""),
        "resolved":    False,
        "won":         None,                        
        "resolve_price": None,
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
    }
    db["predictions"].insert_one(doc)
    return {"ok": True, "saved": True}


# FIXED 2: Changed from "/stats" to "" so fetch('/api/predictions') hits this directly
@router.get("")
def prediction_stats():
    """
    Returns summary stats + the last 50 predictions for the dashboard chart.
    """
    db = get_db()
    
    # We remove {"_id": 0} here because we need the IDs, we'll convert them to strings below
    all_preds = list(db["predictions"].find().sort("created_at", -1))

    resolved   = [p for p in all_preds if p.get("resolved")]
    unresolved = [p for p in all_preds if not p.get("resolved")]
    wins       = [p for p in resolved if p.get("won") is True]
    losses     = [p for p in resolved if p.get("won") is False]

    win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else None

    # Simple flat ROI: each prediction stakes 1 unit.
    total_roi = 0.0
    for p in resolved:
        # Fallback support for older DB entries
        yp = p.get("yes_price", p.get("entry_price", 0.5))
        verdict = p.get("verdict", p.get("ai_verdict"))
        
        if verdict == "BUY_YES":
            total_roi += (1 / yp - 1) if p["won"] else -1
        else:  # BUY_NO
            no_p = 1 - yp
            if no_p > 0:
                total_roi += (1 / no_p - 1) if p["won"] else -1

    roi_pct = round(total_roi / len(resolved) * 100, 1) if resolved else None

    # Cumulative win-rate series for chart
    chart_data = []
    running_wins = 0
    for i, p in enumerate(reversed(resolved[-50:]), 1):
        if p.get("won"):
            running_wins += 1
        chart_data.append({
            "n":       i,
            "win_rate": round(running_wins / i * 100, 1),
            "won":      p.get("won"),
            "verdict":  p.get("verdict", p.get("ai_verdict")),
            "question": p.get("question", "")[:60],
            "edge_pct": p.get("edge_pct"),
        })

    # FIXED 3: Convert variables to exactly what app.js expects to see
    for p in all_preds:
        p["_id"] = str(p["_id"])
        p["ai_verdict"] = p.get("verdict", p.get("ai_verdict"))
        p["entry_price"] = p.get("yes_price", p.get("entry_price"))

    return {
        "predictions": all_preds,  # <--- app.js reads this exact key!
        "total":       len(all_preds),
        "resolved":    len(resolved),
        "unresolved":  len(unresolved),
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    win_rate,
        "roi_pct":     roi_pct,
        "chart_data":  chart_data,
        "recent":      all_preds[:20],
    }