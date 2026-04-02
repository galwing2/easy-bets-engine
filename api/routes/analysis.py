"""
api/routes/analysis.py — AI misprice analysis + debug endpoints.
"""
import requests
from fastapi import APIRouter

from config import GEMINI_API_KEY, GEMINI_URL
from api.ai import analyze, call_gemini
from api.models import AnalyzeRequest

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/analyze-market")
async def analyze_market(body: AnalyzeRequest):
    """
    Lazy-loaded per card. Returns cached MongoDB result (<6 hrs) or calls Gemini.
    """
    result, from_cache = analyze(body.cache_key, body.question, body.yes_price)
    return {"result": result, "from_cache": from_cache}


@router.get("/debug")
def debug():
    """
    Connectivity check — visit /api/debug in your browser.
    Tests that GEMINI_API_KEY is set and the API is reachable.
    """
    if not GEMINI_API_KEY:
        return {
            "status":  "ERROR",
            "problem": "GEMINI_API_KEY not set in .env",
            "fix":     "Add GEMINI_API_KEY=your_key to .env and restart uvicorn",
        }
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
        if not r.ok:
            err = r.json().get("error", {}).get("message", r.text[:300])
            return {
                "status":       "ERROR",
                "http_status":  r.status_code,
                "gemini_error": err,
                "fix":          "Check your key at https://aistudio.google.com/app/apikey",
            }
        parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw   = "".join(p.get("text", "") for p in parts)
        return {
            "status":          "OK",
            "key_prefix":      GEMINI_API_KEY[:8] + "...",
            "gemini_response": raw.strip(),
            "next":            "Key works! Test a full analysis at /api/debug-analyze",
        }
    except Exception as e:
        return {"status": "ERROR", "exception": str(e)}


@router.get("/debug-analyze")
def debug_analyze():
    """
    Fires a real Gemini + web search call. Visit /api/debug-analyze to test.
    """
    result = call_gemini("Will Real Madrid win the Champions League 2025-26?", 0.35)
    return {"result": result}
