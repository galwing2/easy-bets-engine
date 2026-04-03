"""
api/ai.py — Market analysis using Tavily (free web search) + Gemini (free tier).

Flow:
  1. Tavily searches the web for real-time data about the market question
  2. Search results are injected into the Gemini prompt as plain text context
  3. Gemini reasons over the data and returns a structured JSON verdict
  4. Result is cached in MongoDB for 6 hours

This approach requires NO billing on either service:
  - Tavily free tier: 1,000 searches/month  → https://tavily.com
  - Gemini free tier: 1,500 requests/day    → no googleSearch tool needed

Public interface:
    analyze(cache_key, question, yes_price) -> (dict, from_cache: bool)
    call_gemini(question, yes_price) -> dict   (used by debug endpoints)
"""

import re
import json
import hashlib
from datetime import datetime, timezone, timedelta

import requests

from config import GEMINI_API_KEY, GEMINI_URL, TAVILY_API_KEY, ANALYSIS_CACHE_TTL_HOURS
from api.db import get_db


# ── Cache ─────────────────────────────────────────────────────────────────────

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


# ── Step 1: Tavily web search ─────────────────────────────────────────────────

def _tavily_search(question: str) -> str:
    """
    Search the web via Tavily and return a formatted context string.
    Falls back to empty string if Tavily key is missing or call fails.
    """
    if not TAVILY_API_KEY:
        return ""

    query = question.strip().rstrip("?")

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {TAVILY_API_KEY}",
            },
            json={
                "query":               query,
                "search_depth":        "advanced",
                "max_results":         6,
                "include_answer":      True,
                "include_raw_content": False,
            },
            timeout=15,
        )

        if not resp.ok:
            return ""

        data    = resp.json()
        results = data.get("results", [])
        answer  = data.get("answer", "")

        lines = []
        if answer:
            lines.append(f"WEB SUMMARY: {answer}\n")

        for i, r in enumerate(results[:5], 1):
            title   = r.get("title", "")
            url     = r.get("url", "")
            content = r.get("content", "").strip()[:400]
            lines.append(f"[Source {i}] {title}\n{url}\n{content}\n")

        return "\n".join(lines)

    except Exception:
        return ""


# ── Step 2: Gemini reasoning ──────────────────────────────────────────────────

PROMPT_WITH_DATA = """\
You are an expert sports betting analyst. You have been given real-time web \
search results about a prediction market question. Use this data to determine \
whether the market is mispriced.

MARKET QUESTION: "{question}"
Current YES price: {yes_pct}%  |  Current NO price: {no_pct}%

LIVE WEB SEARCH RESULTS:
{web_context}

TASK:
Based on the search results above, determine the true fair probability for \
YES resolving. Identify any mispricing vs the current Polymarket price.

RESPOND with ONLY a valid JSON object — no markdown fences, no extra text:
{{
  "fair_value": <float 0.0-1.0>,
  "confidence": "<low|medium|high>",
  "verdict": "<BUY_YES|BUY_NO|FAIR|SKIP>",
  "edge_pct": <signed float — positive means YES is cheap, e.g. +12.5>,
  "reasoning": "<2-3 sentences citing specific facts from the search results>",
  "key_facts": ["<fact>", "<fact>", "<fact>"],
  "sportsbook_implied": <float 0.0-1.0 or null if not found>
}}

Verdict rules:
- BUY_YES if fair_value > yes_price + 0.05
- BUY_NO  if fair_value < yes_price - 0.05
- FAIR    if within 5 cents of current price
- SKIP    if the search results contain insufficient relevant data
- Set confidence = "high" only if 3+ independent data points support your verdict
- Be specific: name teams, cite scores, rankings, odds from the sources above
"""

PROMPT_NO_DATA = """\
You are an expert sports betting analyst. No live web data was available, \
so use your training knowledge to analyse this market.

MARKET QUESTION: "{question}"
Current YES price: {yes_pct}%  |  Current NO price: {no_pct}%

Based on your knowledge of sports statistics, typical outcomes, team/player \
quality and historical base rates, give your best estimate of fair value.
Note in your reasoning that this is based on training data, not live info.

RESPOND with ONLY a valid JSON object — no markdown fences, no extra text:
{{
  "fair_value": <float 0.0-1.0>,
  "confidence": "low",
  "verdict": "<BUY_YES|BUY_NO|FAIR|SKIP>",
  "edge_pct": <signed float>,
  "reasoning": "<2-3 sentences>",
  "key_facts": ["<fact>", "<fact>"],
  "sportsbook_implied": null
}}
"""


def _call_gemini_raw(prompt: str) -> dict:
    """Send a plain prompt to Gemini — no tools, no billing needed."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set in .env"}

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                # deliberately NO "tools" key — avoids the spending cap
                "generationConfig": {
                    "maxOutputTokens": 1024,
                    "temperature":     0.1,
                },
            },
            timeout=30,
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

        raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        raw = re.sub(r"\n?```$",        "", raw.strip())

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return {"error": f"No JSON in response. Raw: {raw[:400]}"}
            try:
                parsed = json.loads(match.group())
            except Exception as e:
                return {"error": f"JSON parse failed: {e}. Raw: {raw[:400]}"}

        return {
            "fair_value":         float(parsed.get("fair_value", 0.5)),
            "confidence":         str(parsed.get("confidence", "low")),
            "verdict":            str(parsed.get("verdict", "SKIP")),
            "edge_pct":           float(parsed.get("edge_pct", 0)),
            "reasoning":          str(parsed.get("reasoning", "")),
            "key_facts":          list(parsed.get("key_facts", [])),
            "sportsbook_implied": parsed.get("sportsbook_implied"),
        }

    except requests.exceptions.Timeout:
        return {"error": "Gemini timed out (30s). Try again."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Public: call_gemini (also used by debug endpoint) ─────────────────────────

def call_gemini(question: str, yes_price: float) -> dict:
    """Full pipeline: Tavily search → Gemini reasoning."""
    web_context = _tavily_search(question)

    if web_context:
        prompt = PROMPT_WITH_DATA.format(
            question=question,
            yes_pct=round(yes_price * 100),
            no_pct=round((1 - yes_price) * 100),
            web_context=web_context,
        )
    else:
        prompt = PROMPT_NO_DATA.format(
            question=question,
            yes_pct=round(yes_price * 100),
            no_pct=round((1 - yes_price) * 100),
        )

    return _call_gemini_raw(prompt)


# ── Public: analyze (called by route) ────────────────────────────────────────

def analyze(cache_key: str, question: str, yes_price: float) -> tuple[dict, bool]:
    """
    Returns (result_dict, from_cache).
    Checks MongoDB cache first; on miss runs Tavily + Gemini and caches result.
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
