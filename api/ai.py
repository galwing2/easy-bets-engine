"""
api/ai.py — Market analysis using multi-agent AI debate + Tavily web search.

Multi-Agent Flow (Feature 2):
  1. Tavily searches the web for real-time data about the market question.
  2. THREE Gemini calls run in parallel via asyncio:
       • The Bull  — finds every statistical reason YES will happen.
       • The Bear  — finds every statistical reason NO will happen.
       • The Judge — reads Bull + Bear outputs and determines fair value.
  3. Result is cached in MongoDB for 6 hours.

Public interface:
    analyze(cache_key, question, yes_price) -> (dict, from_cache: bool)
    call_gemini(question, yes_price)         -> dict
"""

import re
import json
import asyncio
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


# ── Step 2: Multi-Agent Prompts ───────────────────────────────────────────────

BULL_PROMPT = """\
You are THE BULL — a sports betting analyst whose only job is to build the \
strongest possible case for YES resolving.

MARKET QUESTION: "{question}"
Current YES price: {yes_pct}%

LIVE WEB SEARCH RESULTS:
{web_context}

TASK: List every statistical, situational, and momentum-based reason why YES \
is likely to happen. Be specific — cite teams, scores, injury reports, odds \
from the sources. Do NOT hedge or mention reasons for NO.

Respond with ONLY a JSON object — no markdown fences:
{{
  "bull_case": "<3-5 sentences making the strongest YES argument>",
  "bull_facts": ["<specific fact>", "<specific fact>", "<specific fact>"],
  "bull_implied_prob": <float 0.0-1.0, your estimate of true YES probability>
}}
"""

BEAR_PROMPT = """\
You are THE BEAR — a sports betting analyst whose only job is to build the \
strongest possible case for NO resolving.

MARKET QUESTION: "{question}"
Current YES price: {yes_pct}%

LIVE WEB SEARCH RESULTS:
{web_context}

TASK: List every statistical, situational, and momentum-based reason why NO \
is likely to happen. Be specific — cite teams, scores, injury reports, odds \
from the sources. Do NOT hedge or mention reasons for YES.

Respond with ONLY a JSON object — no markdown fences:
{{
  "bear_case": "<3-5 sentences making the strongest NO argument>",
  "bear_facts": ["<specific fact>", "<specific fact>", "<specific fact>"],
  "bear_implied_prob": <float 0.0-1.0, your estimate of true YES probability>
}}
"""

JUDGE_PROMPT = """\
You are THE JUDGE — a senior hedge-fund quant who weighs both sides of a \
prediction market debate and determines the true fair value.

MARKET QUESTION: "{question}"
Current Polymarket YES price: {yes_pct}%  |  NO price: {no_pct}%

THE BULL SAYS:
{bull_case}
Bull's key facts: {bull_facts}
Bull's implied YES probability: {bull_prob}%

THE BEAR SAYS:
{bear_case}
Bear's key facts: {bear_facts}
Bear's implied YES probability: {bear_prob}%

TASK: Weigh the bull and bear arguments critically. Determine the true fair \
value for YES. Identify which side has stronger evidence and why.

Verdict rules:
- BUY_YES if fair_value > yes_price + 0.05
- BUY_NO  if fair_value < yes_price - 0.05
- FAIR    if within 5 cents of current price
- SKIP    if arguments are contradictory or evidence is too thin
- confidence = "high" only if both sides agree on direction OR one side has \
  clearly superior data

RESPOND with ONLY a valid JSON object — no markdown fences, no extra text:
{{
  "fair_value": <float 0.0-1.0>,
  "confidence": "<low|medium|high>",
  "verdict": "<BUY_YES|BUY_NO|FAIR|SKIP>",
  "edge_pct": <signed float — positive means YES is cheap>,
  "reasoning": "<2-3 sentences citing the strongest facts from both sides>",
  "key_facts": ["<most important fact>", "<2nd fact>", "<3rd fact>"],
  "sportsbook_implied": <float 0.0-1.0 or null>,
  "bull_summary": "<1 sentence>",
  "bear_summary": "<1 sentence>"
}}
"""

SINGLE_AGENT_PROMPT = """\
You are an expert sports betting analyst. Use the data below to determine \
whether this prediction market is mispriced.

MARKET QUESTION: "{question}"
Current YES price: {yes_pct}%  |  Current NO price: {no_pct}%

LIVE WEB SEARCH RESULTS:
{web_context}

RESPOND with ONLY a valid JSON object — no markdown fences:
{{
  "fair_value": <float 0.0-1.0>,
  "confidence": "<low|medium|high>",
  "verdict": "<BUY_YES|BUY_NO|FAIR|SKIP>",
  "edge_pct": <signed float>,
  "reasoning": "<2-3 sentences citing specific facts>",
  "key_facts": ["<fact>", "<fact>", "<fact>"],
  "sportsbook_implied": <float 0.0-1.0 or null>
}}

Verdict rules:
- BUY_YES if fair_value > yes_price + 0.05
- BUY_NO  if fair_value < yes_price - 0.05
- FAIR    if within 5 cents of current price
- SKIP    if insufficient data
- confidence = "high" only if 3+ independent data points support the verdict
"""

NO_DATA_PROMPT = """\
You are an expert sports betting analyst. No live web data was available, \
so use your training knowledge.

MARKET QUESTION: "{question}"
Current YES price: {yes_pct}%  |  Current NO price: {no_pct}%

Give your best estimate of fair value based on historical base rates and \
general knowledge. Note in reasoning that this uses training data only.

RESPOND with ONLY a valid JSON object — no markdown fences:
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


# ── Gemini HTTP call (sync) ───────────────────────────────────────────────────

def _call_gemini_raw(prompt: str, max_tokens: int = 4096) -> dict:
    """Send a plain prompt to Gemini. Returns parsed dict or {"error": ...}."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set in .env"}

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature":     0.1,
                    "responseMimeType": "application/json",
                },
                "safetySettings": [
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HARASSMENT",         "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH",        "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",  "threshold": "BLOCK_NONE"},
                ],
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
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return {"error": f"No JSON in response. Raw: {raw[:400]}"}
            try:
                return json.loads(match.group())
            except Exception as e:
                return {"error": f"JSON parse failed: {e}. Raw: {raw[:400]}"}

    except requests.exceptions.Timeout:
        return {"error": "Gemini timed out (30s). Try again."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Async wrapper so Bull + Bear run in parallel ──────────────────────────────

async def _call_gemini_async(prompt: str, max_tokens: int = 2048) -> dict:
    """Runs the blocking _call_gemini_raw in a thread so asyncio can parallelize."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _call_gemini_raw(prompt, max_tokens))


# ── Multi-Agent Debate Pipeline ───────────────────────────────────────────────

async def _multi_agent_debate(question: str, yes_price: float, web_context: str) -> dict:
    """
    Runs Bull and Bear prompts in parallel, then feeds their output to the Judge.
    Falls back to single-agent if either side errors.
    """
    yes_pct = round(yes_price * 100)
    no_pct  = round((1 - yes_price) * 100)

    bull_prompt = BULL_PROMPT.format(
        question=question, yes_pct=yes_pct,
        web_context=web_context or "(no live data available)"
    )
    bear_prompt = BEAR_PROMPT.format(
        question=question, yes_pct=yes_pct,
        web_context=web_context or "(no live data available)"
    )

    # Run Bull and Bear concurrently
    bull_raw, bear_raw = await asyncio.gather(
        _call_gemini_async(bull_prompt),
        _call_gemini_async(bear_prompt),
    )

    # If either side errored, fall back to single-agent
    if "error" in bull_raw or "error" in bear_raw:
        fallback_prompt = (SINGLE_AGENT_PROMPT if web_context else NO_DATA_PROMPT).format(
            question=question, yes_pct=yes_pct, no_pct=no_pct,
            web_context=web_context,
        )
        result = _call_gemini_raw(fallback_prompt)
        result["debate_mode"] = False
        return result

    bull_prob = round((bull_raw.get("bull_implied_prob", yes_price)) * 100)
    bear_prob = round((bear_raw.get("bear_implied_prob", yes_price)) * 100)

    judge_prompt = JUDGE_PROMPT.format(
        question=question,
        yes_pct=yes_pct,
        no_pct=no_pct,
        bull_case=bull_raw.get("bull_case", ""),
        bull_facts=json.dumps(bull_raw.get("bull_facts", [])),
        bull_prob=bull_prob,
        bear_case=bear_raw.get("bear_case", ""),
        bear_facts=json.dumps(bear_raw.get("bear_facts", [])),
        bear_prob=bear_prob,
    )

    judge_raw = _call_gemini_raw(judge_prompt, max_tokens=4096)

    if "error" in judge_raw:
        return judge_raw

    return {
        "fair_value":         float(judge_raw.get("fair_value", 0.5)),
        "confidence":         str(judge_raw.get("confidence", "low")),
        "verdict":            str(judge_raw.get("verdict", "SKIP")),
        "edge_pct":           float(judge_raw.get("edge_pct", 0)),
        "reasoning":          str(judge_raw.get("reasoning", "")),
        "key_facts":          list(judge_raw.get("key_facts", [])),
        "sportsbook_implied": judge_raw.get("sportsbook_implied"),
        # Debate-specific fields shown in the UI
        "debate_mode":   True,
        "bull_summary":  str(bull_raw.get("bull_case", "")[:200]),
        "bear_summary":  str(bear_raw.get("bear_case", "")[:200]),
        "bull_prob":     bull_prob,
        "bear_prob":     bear_prob,
    }


# ── Public: call_gemini (also used by debug endpoint) ─────────────────────────

async def call_gemini(question: str, yes_price: float) -> dict:
    """Full pipeline: Tavily search → multi-agent debate → structured verdict."""
    web_context = _tavily_search(question)

    # Natively await the async debate pipeline using FastAPI's event loop
    try:
        result = await _multi_agent_debate(question, yes_price, web_context)
    except Exception as e:
        result = {"error": f"Debate pipeline failed: {e}"}

    return result


# ── Public: analyze (called by route) ────────────────────────────────────────

async def analyze(cache_key: str, question: str, yes_price: float) -> tuple[dict, bool]:
    """
    Returns (result_dict, from_cache).
    Checks MongoDB cache first; on miss runs Tavily + multi-agent debate and caches.
    """
    if not cache_key:
        cache_key = hashlib.md5(question.encode()).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        return cached, True

    # Now we await the gemini call
    result = await call_gemini(question, yes_price)
    if "error" not in result:
        _cache_set(cache_key, result)

    return result, False