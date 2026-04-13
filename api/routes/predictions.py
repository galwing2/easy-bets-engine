"""
api/routes/predictions.py — AI prediction track record: save, update, and serve.
"""
from datetime import datetime, timezone
from fastapi import APIRouter
from api.db import get_db

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


@router.post("/save")
def save_prediction(body: dict):
    db        = get_db()
    cache_key = body.get("cache_key")
    if not cache_key:
        return {"ok": False, "reason": "missing cache_key"}

    if db["predictions"].find_one({"cache_key": cache_key}):
        return {"ok": True, "skipped": True}

    db["predictions"].insert_one({
        "cache_key":     cache_key,
        "question":      body.get("question", ""),
        "market_slug":   body.get("market_slug", ""),
        "yes_price":     body.get("yes_price"),
        "entry_price":   body.get("yes_price"),
        "verdict":       body.get("verdict"),
        "ai_verdict":    body.get("verdict"),
        "fair_value":    body.get("fair_value"),
        "edge_pct":      body.get("edge_pct"),
        "confidence":    body.get("confidence"),
        "end_date":      body.get("end_date", ""),
        "resolved":      False,
        "won":           None,
        "resolve_price": None,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "resolved_at":   None,
    })
    return {"ok": True, "saved": True}


@router.get("")
def prediction_stats():
    db = get_db()

    # Sort in Python to handle mixed created_at types safely
    all_preds = list(db["predictions"].find())
    all_preds.sort(key=lambda p: str(p.get("created_at", "")), reverse=True)

    resolved   = [p for p in all_preds if p.get("resolved") is True]
    unresolved = [p for p in all_preds if not p.get("resolved")]
    wins       = [p for p in resolved if p.get("won") is True]
    losses     = [p for p in resolved if p.get("won") is False]

    # ── Win rate ──────────────────────────────────────────────────────────────
    win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else None

    # ── ROI — guarded against None/zero ──────────────────────────────────────
    total_roi = 0.0
    for p in resolved:
        yp      = p.get("yes_price") or p.get("entry_price") or 0.5
        verdict = p.get("verdict") or p.get("ai_verdict") or ""
        try:
            if verdict == "BUY_YES":
                total_roi += (1 / yp - 1) if p.get("won") else -1
            elif verdict == "BUY_NO":
                no_p = 1 - yp
                total_roi += (1 / no_p - 1) if p.get("won") else -1
        except (ZeroDivisionError, TypeError):
            continue

    roi_pct = round(total_roi / len(resolved) * 100, 1) if resolved else None

    # ── Avg edge on BUY calls ────────────────────────────────────────────────
    edges = [p.get("edge_pct") for p in all_preds if p.get("edge_pct") is not None]
    avg_edge = round(sum(edges) / len(edges), 1) if edges else None

    # ── Confidence breakdown ──────────────────────────────────────────────────
    conf_counts = {"high": 0, "medium": 0, "low": 0}
    for p in all_preds:
        c = (p.get("confidence") or "low").lower()
        if c in conf_counts:
            conf_counts[c] += 1

    # ── Win rate by verdict type ──────────────────────────────────────────────
    yes_calls  = [p for p in resolved if (p.get("verdict") or p.get("ai_verdict")) == "BUY_YES"]
    no_calls   = [p for p in resolved if (p.get("verdict") or p.get("ai_verdict")) == "BUY_NO"]
    yes_wr = round(sum(1 for p in yes_calls if p.get("won")) / len(yes_calls) * 100, 1) if yes_calls else None
    no_wr  = round(sum(1 for p in no_calls  if p.get("won")) / len(no_calls)  * 100, 1) if no_calls  else None

    # ── Cumulative win-rate series for sparkline chart ────────────────────────
    chart_data = []
    running_wins = 0
    for i, p in enumerate(reversed(resolved[-50:]), 1):
        if p.get("won"):
            running_wins += 1
        chart_data.append({
            "n":        i,
            "win_rate": round(running_wins / i * 100, 1),
            "won":      p.get("won"),
            "verdict":  p.get("verdict") or p.get("ai_verdict"),
            "question": (p.get("question") or "")[:60],
            "edge_pct": p.get("edge_pct"),
        })

    # ── Normalise fields for frontend ─────────────────────────────────────────
    for p in all_preds:
        p["_id"]         = str(p["_id"])
        p["ai_verdict"]  = p.get("verdict") or p.get("ai_verdict")
        p["entry_price"] = p.get("yes_price") or p.get("entry_price")

    return {
        "predictions": all_preds,
        "recent":      all_preds[:20],
        # Summary stats
        "total":       len(all_preds),
        "resolved":    len(resolved),
        "unresolved":  len(unresolved),
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    win_rate,
        "roi_pct":     roi_pct,
        "avg_edge":    avg_edge,
        "conf_counts": conf_counts,
        "yes_win_rate": yes_wr,
        "no_win_rate":  no_wr,
        "chart_data":  chart_data,
    }