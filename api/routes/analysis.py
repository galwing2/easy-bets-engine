"""
api/routes/analysis.py — AI misprice analysis + debug endpoints.
"""
import requests
from fastapi import APIRouter
from datetime import datetime, timezone

from config import GEMINI_API_KEY, GEMINI_URL, TAVILY_API_KEY
from api.ai import analyze, call_gemini
from api.models import AnalyzeRequest
from api.db import get_db

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze-market")
async def analyze_market(body: AnalyzeRequest):
    """
    Lazy-loaded per card. Returns cached MongoDB result (<6 hrs) or calls Gemini.
    Auto-saves high/medium-confidence BUY verdicts to the predictions collection.
    Removes pending predictions if re-analyzed as FAIR.
    """
    result, from_cache = await analyze(body.cache_key, body.question, body.yes_price)

    # Auto-save, refresh, or remove predictions for the track record.
    if not from_cache and "error" not in result:
        verdict    = result.get("verdict", "")
        confidence = result.get("confidence", "low")
        
        db  = get_db()
        now = datetime.now(timezone.utc).isoformat()
        existing = db["predictions"].find_one({"cache_key": body.cache_key})

        # Check if it's a valid bet
        if verdict in ("BUY_YES", "BUY_NO") and confidence in ("high", "medium"):
            if existing and existing.get("resolved"):
                # Already resolved — don't overwrite the final outcome
                pass
            elif existing:
                # Pending — refresh with latest AI result and End Date
                db["predictions"].update_one(
                    {"cache_key": body.cache_key},
                    {"$set": {
                        "verdict":     verdict,
                        "ai_verdict":  verdict,
                        "fair_value":  result.get("fair_value"),
                        "edge_pct":    result.get("edge_pct"),
                        "confidence":  confidence,
                        "yes_price":   body.yes_price,
                        "entry_price": body.yes_price,
                        "end_date":    body.end_date,
                        "market_slug": body.market_slug,  # <-- ADD THIS LINE
                        "analyzed_at": now,
                    }}
                )
            else:
                # New prediction
                db["predictions"].insert_one({
                    "cache_key":     body.cache_key,
                    "question":      body.question,
                    "market_slug":   body.market_slug,
                    "yes_price":     body.yes_price,
                    "entry_price":   body.yes_price,
                    "verdict":       verdict,
                    "ai_verdict":    verdict,
                    "fair_value":    result.get("fair_value"),
                    "edge_pct":      result.get("edge_pct"),
                    "confidence":    confidence,
                    "end_date":      body.end_date,
                    "resolved":      False,
                    "won":           None,
                    "resolve_price": None,
                    "created_at":    now,
                    "analyzed_at":   now,
                    "resolved_at":   None,
                })
        else:
            # Result is FAIR, SKIP, or low confidence. 
            # If this market was previously saved as a pending bet, delete it.
            if existing and not existing.get("resolved"):
                db["predictions"].delete_one({"cache_key": body.cache_key})

    return {"result": result, "from_cache": from_cache}


@router.get("/debug")
def debug():
    """
    Full connectivity check. Visit /api/debug in your browser.
    Tests both Gemini and Tavily keys.
    """
    out = {}

    # ── Check Tavily ──────────────────────────────────────────────────────────
    if not TAVILY_API_KEY:
        out["tavily"] = {
            "status":  "MISSING",
            "fix":     "Add TAVILY_API_KEY to .env — get a free key at https://tavily.com",
        }
    else:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"Bearer {TAVILY_API_KEY}",
                },
                json={"query": "test", "max_results": 1},
                timeout=10,
            )
            if r.ok:
                out["tavily"] = {"status": "OK", "key_prefix": TAVILY_API_KEY[:8] + "..."}
            else:
                err = r.json().get("detail", r.text[:200])
                out["tavily"] = {"status": "ERROR", "http_status": r.status_code, "detail": err}
        except Exception as e:
            out["tavily"] = {"status": "ERROR", "exception": str(e)}

    # ── Check Gemini (no tools — free tier) ───────────────────────────────────
    if not GEMINI_API_KEY:
        out["gemini"] = {
            "status": "MISSING",
            "fix":    "Add GEMINI_API_KEY to .env — get a free key at https://aistudio.google.com/app/apikey",
        }
    else:
        try:
            r = requests.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": 'Reply with exactly: {"ok":true}'}]}],
                    "generationConfig": {"maxOutputTokens": 20, "temperature": 0},
                },
                timeout=15,
            )
            if r.ok:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                raw   = "".join(p.get("text", "") for p in parts)
                out["gemini"] = {
                    "status":          "OK",
                    "key_prefix":      GEMINI_API_KEY[:8] + "...",
                    "gemini_response": raw.strip(),
                }
            else:
                err = r.json().get("error", {}).get("message", r.text[:200])
                out["gemini"] = {
                    "status":      "ERROR",
                    "http_status": r.status_code,
                    "detail":      err,
                    "fix":         "Check your key at https://aistudio.google.com/app/apikey",
                }
        except Exception as e:
            out["gemini"] = {"status": "ERROR", "exception": str(e)}

    overall = "OK" if all(v.get("status") == "OK" for v in out.values()) else "ISSUES"
    return {"overall": overall, **out}


@router.get("/debug-analyze")
def debug_analyze():
    """
    Fires a real Tavily + Gemini call. Visit /api/debug-analyze to test end-to-end.
    """
    result = call_gemini("Will Real Madrid win the Champions League 2025-26?", 0.35)
    return {"result": result}