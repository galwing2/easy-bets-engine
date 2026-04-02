"""
api/ai.py — Gemini 2.5 Flash analysis with googleSearch grounding.

Public interface:
    analyze(cache_key, question, yes_price) -> dict
        Returns cached result if fresh, otherwise calls Gemini and caches.

    call_gemini(question, yes_price) -> dict
        Raw Gemini call — used by routes and debug endpoints.
"""
import re
import json
import hashlib
from datetime import datetime, timezone, timedelta

import requests

from config import GEMINI_API_KEY, GEMINI_URL, ANALYSIS_CACHE_TTL_HOURS
from api.db import get_db


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_get(cache_key: str) -> dict | None:
    doc = get_db()["market_analysis"].find_one({"cache_key": cache_key})
    if not doc:
        return None
    cached_at = doc.get("cached_at")
    if not cached_at:
        return None
    if isinstance(cached_at, str):
        cached_at = datetime.fromisoformat(cached_at)
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - cached_at > timedelta(hours=ANALYSIS_CACHE_TTL_HOURS):
        return None
    doc.pop("_id", None)
    doc.pop("cached_at", None)
    return doc


def _cache_set(cache_key: str, analysis: dict) -> None:
    get_db()["market_analysis"].replace_one(
        {"cache_key": cache_key},
        {"cache_key": cache_key, "cached_at": datetime.now(timezone.utc), **analysis},
        upsert=True,
    )


# ── Gemini call ───────────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """\
You are an expert sports betting analyst with live web search access.

MARKET: "{question}"
Current YES price: {yes_pct}%   |   Current NO price: {no_pct}%

TASK:
1. Search the web for all relevant data: team/player current form, \
rankings/ratings, injury news, head-to-head record, expert predictions, \
sportsbook consensus odds.
2. Determine the TRUE fair probability for YES resolving.
3. Identify whether this market is mispriced vs the current Polymarket price.

RESPOND with ONLY a valid JSON object — no markdown fences, no preamble:
{{
  "fair_value": <float 0.0-1.0>,
  "confidence": "<low|medium|high>",
  "verdict": "<BUY_YES|BUY_NO|FAIR|SKIP>",
  "edge_pct": <signed float, e.g. +12.5 means YES is 12.5 cents cheap>,
  "reasoning": "<2-3 sentences — direct, cite specific facts found>",
  "key_facts": ["<fact with source>", "<fact>", "<fact>"],
  "sportsbook_implied": <float 0.0-1.0 or null if not found>
}}

Verdict rules:
- BUY_YES if fair_value > yes_price + 0.05
- BUY_NO  if fair_value < yes_price - 0.05
- FAIR    if within 5 cents
- SKIP    if insufficient data found
- confidence = "high" only if you found 3+ solid independent data points
"""


def call_gemini(question: str, yes_price: float) -> dict:
    """Fire a Gemini + googleSearch call. Returns a structured dict or {error: ...}."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set in .env"}

    prompt = PROMPT_TEMPLATE.format(
        question=question,
        yes_pct=round(yes_price * 100),
        no_pct=round((1 - yes_price) * 100),
    )

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"googleSearch": {}}],
                "generationConfig": {"maxOutputTokens": 4096, "temperature": 0.1},
            },
            timeout=45,
        )

        if not resp.ok:
            try:
                msg = resp.json().get("error", {}).get("message", resp.text[:300])
            except Exception:
                msg = resp.text[:300]
            return {"error": f"Gemini HTTP {resp.status_code}: {msg}"}

        data       = resp.json()
        candidates = data.get("candidates", [])

        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            return {"error": f"Gemini returned no candidates: {reason}"}

        parts = candidates[0].get("content", {}).get("parts", [])
        raw   = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()

        if not raw:
            finish = candidates[0].get("finishReason", "unknown")
            return {"error": f"Gemini returned empty text. finishReason={finish}"}

        # Strip markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$",        "", raw.strip())

        # Parse JSON — try direct first, then extract from prose
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return {"error": f"No JSON in Gemini response. Raw: {raw[:400]}"}
            try:
                parsed = json.loads(match.group())
            except Exception as e:
                return {"error": f"JSON parse failed: {e}. Raw: {raw[:400]}"}

        return {
            "fair_value":         float(parsed.get("fair_value", yes_price)),
            "confidence":         str(parsed.get("confidence", "low")),
            "verdict":            str(parsed.get("verdict", "SKIP")),
            "edge_pct":           float(parsed.get("edge_pct", 0)),
            "reasoning":          str(parsed.get("reasoning", "")),
            "key_facts":          list(parsed.get("key_facts", [])),
            "sportsbook_implied": parsed.get("sportsbook_implied"),
        }

    except requests.exceptions.Timeout:
        return {"error": "Gemini timed out (45s). Try again."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Public entry point ────────────────────────────────────────────────────────

def analyze(cache_key: str, question: str, yes_price: float) -> tuple[dict, bool]:
    """
    Returns (result_dict, from_cache).
    Checks MongoDB first; calls Gemini on miss and caches the result.
    """
    if not cache_key:
        cache_key = hashlib.md5(question.encode()).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        return cached, True

    result = call_gemini(question, yes_price)
    if "error" not in result:
        _cache_set(cache_key, result)

    return result, False
