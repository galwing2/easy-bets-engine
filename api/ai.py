import re
import json
import asyncio
import hashlib
import time
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

# ── Math Parsers ─────────────────────────────────────────────────────────────

def parse_odds_to_prob(odds_str: str) -> float | None:
    """Converts American, Fractional, Decimal, or Percentage odds to a probability."""
    if not odds_str or not isinstance(odds_str, str):
        return None
    
    odds = odds_str.strip().replace(',', '')
    
    # 1. Percentage (e.g., "8.5%")
    if '%' in odds:
        try: return float(re.sub(r'[^\d.]', '', odds)) / 100.0
        except: pass
        
    # 2. American Odds (e.g., "+1100" or "-150")
    match = re.search(r'([+-])(\d+)', odds)
    if match:
        sign, val = match.groups()
        val = float(val)
        if sign == '+' and val > 0: return 100 / (val + 100)
        if sign == '-' and val > 0: return val / (val + 100)

    # 3. Fractional (e.g., "11/1")
    match = re.search(r'(\d+)\s*/\s*(\d+)', odds)
    if match:
        num, den = map(float, match.groups())
        if den > 0: return den / (num + den)

    # 4. Decimal (e.g., "12.0")
    try:
        val = float(re.sub(r'[^\d.]', '', odds))
        if val > 1.0: return 1.0 / val
    except: pass

    return None

# ── Step 1: Tavily web search ─────────────────────────────────────────────────

def _tavily_search(question: str) -> str:
    """Search the web via Tavily and return a formatted context string."""
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
        if not resp.ok: return ""
        data    = resp.json()
        results = data.get("results", [])
        answer  = data.get("answer", "")
        lines = []
        if answer: lines.append(f"WEB SUMMARY: {answer}\n")
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
You are THE JUDGE — a data extractor analyzing sports betting information.

MARKET QUESTION: "{question}"

THE BULL SAYS: {bull_case}
THE BEAR SAYS: {bear_case}

TASK 1: Read the arguments and extract any specific traditional sportsbook odds mentioned for YES (e.g., "+1100", "11/1", "12.0", "8%"). If none are mentioned, return null.
TASK 2: Score the fundamental strength of the Bull case from 0-5 (0=no evidence, 5=overwhelming statistical proof).
TASK 3: Score the fundamental strength of the Bear case from 0-5.

RESPOND with ONLY a valid JSON object — no markdown fences:
{{
  "extracted_sportsbook_odds": "<string or null>",
  "bull_score": <int 0-5>,
  "bear_score": <int 0-5>,
  "reasoning": "<2-3 sentences justifying the scores>",
  "key_facts": ["<most important fact>", "<2nd fact>", "<3rd fact>"]
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

# ── Gemini HTTP call (sync with automatic retries) ────────────────────────────

def _call_gemini_raw(prompt: str, max_tokens: int = 4096) -> dict:
    """Send a plain prompt to Gemini. Includes automatic retries for 503/429 errors."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set in .env"}

    max_retries = 3
    for attempt in range(max_retries):
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

            if resp.status_code in [503, 429]:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt) 
                    continue
                else:
                    return {"error": f"Gemini HTTP {resp.status_code}: Service Overloaded. Please try again."}

            if not resp.ok:
                try: msg = resp.json().get("error", {}).get("message", resp.text[:300])
                except: msg = resp.text[:300]
                return {"error": f"Gemini HTTP {resp.status_code}: {msg}"}

            data       = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return {"error": "Gemini returned no candidates"}

            parts = candidates[0].get("content", {}).get("parts", [])
            raw   = "\n".join(p.get("text", "") for p in parts if "text" in p).strip()

            raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
            raw = re.sub(r"\n?```$",        "", raw.strip())

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    try: return json.loads(match.group())
                    except: pass
                return {"error": f"No JSON in response."}

        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return {"error": "Gemini timed out (30s)."}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    return {"error": "Unknown error during API call."}

async def _call_gemini_async(prompt: str, max_tokens: int = 2048) -> dict:
    """Runs the blocking _call_gemini_raw in a thread so asyncio can parallelize."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _call_gemini_raw(prompt, max_tokens))

# ── Multi-Agent Debate Pipeline ───────────────────────────────────────────────

async def _multi_agent_debate(question: str, yes_price: float, web_context: str) -> dict:
    """Runs Bull, Bear, and Judge sequentially with deterministic math logic."""
    yes_pct = round(yes_price * 100)
    no_pct  = round((1 - yes_price) * 100)

    bull_prompt = BULL_PROMPT.format(question=question, yes_pct=yes_pct, web_context=web_context or "(no data)")
    bear_prompt = BEAR_PROMPT.format(question=question, yes_pct=yes_pct, web_context=web_context or "(no data)")

    # 1. Ask Bull
    bull_raw = await _call_gemini_async(bull_prompt)
    await asyncio.sleep(2)
    
    # 2. Ask Bear
    bear_raw = await _call_gemini_async(bear_prompt)

    # Single-Agent Fallback
    if "error" in bull_raw or "error" in bear_raw:
        fallback_prompt = (SINGLE_AGENT_PROMPT if web_context else NO_DATA_PROMPT).format(
            question=question, yes_pct=yes_pct, no_pct=no_pct, web_context=web_context
        )
        await asyncio.sleep(2)
        result = await _call_gemini_async(fallback_prompt)
        
        # Override fallback math
        if "error" not in result:
            fv = float(result.get("fair_value", yes_price))
            edge = (fv - yes_price) * 100
            result["fair_value"] = fv
            result["edge_pct"]   = edge
            if edge >= 2.0: result["verdict"] = "BUY_YES"
            elif edge <= -2.0: result["verdict"] = "BUY_NO"
            else: result["verdict"] = "FAIR"
                
        result["debate_mode"] = False
        return result

    bull_prob = round((bull_raw.get("bull_implied_prob", yes_price)) * 100)
    bear_prob = round((bear_raw.get("bear_implied_prob", yes_price)) * 100)

    judge_prompt = JUDGE_PROMPT.format(
        question=question,
        bull_case=bull_raw.get("bull_case", ""),
        bear_case=bear_raw.get("bear_case", "")
    )

    await asyncio.sleep(2)
    
    # 3. Ask Judge
    judge_raw = await _call_gemini_async(judge_prompt, max_tokens=4096)
    if "error" in judge_raw:
        return judge_raw

    # --- DETERMINISTIC PRICING MATH ---
    sb_prob = parse_odds_to_prob(judge_raw.get("extracted_sportsbook_odds"))
    
    if sb_prob is not None:
        anchor_price = sb_prob * 0.95  # De-vig
        confidence = "high"
    else:
        anchor_price = yes_price
        confidence = "medium"

    bull_score = int(judge_raw.get("bull_score", 0))
    bear_score = int(judge_raw.get("bear_score", 0))
    
    # 1. Increased max LLM shift from 3% to 5%
    score_diff = bull_score - bear_score
    qualitative_shift = (score_diff / 5.0) * 0.05 
    
    fair_value = anchor_price + qualitative_shift
    fair_value = max(0.01, min(0.99, fair_value))
    
    edge_pct = fair_value - yes_price
    
    # 2. Lowered BUY trigger threshold from 5% down to 2%
    if edge_pct >= 0.02:
        verdict = "BUY_YES"
    elif edge_pct <= -0.02:
        verdict = "BUY_NO"
    else:
        verdict = "FAIR"

    return {
        "fair_value":         float(fair_value),
        "confidence":         confidence,
        "verdict":            verdict,
        "edge_pct":           float(edge_pct * 100),
        "reasoning":          str(judge_raw.get("reasoning", "")),
        "key_facts":          list(judge_raw.get("key_facts", [])),
        "sportsbook_implied": sb_prob,
        "debate_mode":        True,
        "bull_summary":       str(bull_raw.get("bull_case", "")[:200]),
        "bear_summary":       str(bear_raw.get("bear_case", "")[:200]),
        "bull_prob":          bull_prob,
        "bear_prob":          bear_prob,
    }

# ── Public Endpoints ────────────────────────────────────────────────────────

async def call_gemini(question: str, yes_price: float) -> dict:
    """Full pipeline: Tavily search → multi-agent debate → structured verdict."""
    web_context = _tavily_search(question)
    try:
        result = await _multi_agent_debate(question, yes_price, web_context)
    except Exception as e:
        result = {"error": f"Debate pipeline failed: {e}"}
    return result

async def analyze(cache_key: str, question: str, yes_price: float) -> tuple[dict, bool]:
    """Returns (result_dict, from_cache). Checks cache first."""
    if not cache_key:
        cache_key = hashlib.md5(question.encode()).hexdigest()

    cached = _cache_get(cache_key)
    if cached:
        return cached, True

    result = await call_gemini(question, yes_price)
    if "error" not in result:
        _cache_set(cache_key, result)

    return result, False