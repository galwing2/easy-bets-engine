"""
api/main.py  —  single process, single command
-----------------------------------------------
Run:   uvicorn api.main:app --reload
Open:  http://localhost:8000
"""

import os, uuid, json, joblib
import numpy as np
import requests as req
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient, ReturnDocument
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── MongoDB ───────────────────────────────────────────────────────────────────
_client = None
def get_db():
    global _client
    if not _client:
        uri = os.getenv("MONGO_URI")
        _client = MongoClient(uri)
    return _client["easy_bets"]

# ── ML model ──────────────────────────────────────────────────────────────────
_model = None
@app.on_event("startup")
def _load():
    global _model
    p = "model/xgb_calibrated.joblib"
    if os.path.exists(p):
        _model = joblib.load(p)
        print("✅ model loaded")

def _bucket(p):
    return 0 if p<.1 else 1 if p<.3 else 2 if p<.7 else 3 if p<.9 else 4

def _parse(v):
    if isinstance(v, str):
        try: return json.loads(v)
        except: return []
    return v if isinstance(v, list) else []

# ── Sports keyword filter ─────────────────────────────────────────────────────
SPORTS_KEYWORDS = [
    "nfl","nba","mlb","nhl","mls","ufc","nascar","pga",
    "football","basketball","baseball","hockey","soccer","tennis","golf","boxing","mma",
    "super bowl","world series","nba finals","stanley cup","champions league",
    "premier league","la liga","bundesliga","serie a","ligue 1","world cup","euro",
    "copa america","playoff","playoffs","semifinal","final","tournament","championship",
    "qualify","relegation","transfer","match","game","season","roster",
    "quarterback","touchdown","goal","homerun","slam dunk","hat trick",
    # Common teams/names that signal sports
    "lakers","celtics","patriots","chiefs","yankees","dodgers","barcelona",
    "real madrid","manchester","liverpool","arsenal","chelsea","juventus","psg",
]

def is_sports_market(title: str, tags: list) -> bool:
    t = title.lower()
    tag_labels = [tg.get("label", "").lower() for tg in (tags or [])]
    return (
        any(kw in t for kw in SPORTS_KEYWORDS) or
        any(kw in " ".join(tag_labels) for kw in ["sport","soccer","nba","nfl","nhl","mlb","tennis","golf","ufc"])
    )

# ── Session endpoints ─────────────────────────────────────────────────────────
class Profile(BaseModel):
    interests: List[str]
    risk_profile: Optional[str] = "mix"
    specific: Optional[str] = ""
    name: Optional[str] = ""

@app.post("/session/start")
def session_start():
    sid = str(uuid.uuid4())
    get_db()["sessions"].insert_one({
        "session_id": sid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile": None,
        "done": False,
    })
    return {"session_id": sid}

@app.get("/session/{sid}")
def session_get(sid: str):
    doc = get_db()["sessions"].find_one({"session_id": sid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "not found")
    return doc

@app.post("/session/{sid}/profile")
def session_save(sid: str, p: Profile):
    doc = get_db()["sessions"].find_one_and_update(
        {"session_id": sid},
        {"$set": {"profile": p.model_dump(), "done": True,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(404, "not found")
    return {"session_id": sid, "profile": doc["profile"]}

@app.delete("/session/{sid}/profile")
def session_reset(sid: str):
    get_db()["sessions"].update_one(
        {"session_id": sid},
        {"$set": {"profile": None, "done": False}}
    )
    return {"reset": True}

# ── Markets ───────────────────────────────────────────────────────────────────
GAMMA = "https://gamma-api.polymarket.com/events?closed=false&limit=50&order=volume&ascending=false"

@app.post("/api/markets")
def markets(body: dict):
    profile      = body.get("profile", {})
    interests    = [i.lower() for i in profile.get("interests", [])]
    sports_only  = body.get("sports_only", False)

    try:
        events = req.get(GAMMA, timeout=8).json()
    except Exception as e:
        raise HTTPException(502, str(e))

    out = []
    for event in events:
        title = event.get("title", "")
        tags  = event.get("tags") or []

        # Sports-only mode: skip non-sports events entirely
        if sports_only and not is_sports_market(title, tags):
            continue

        # Interest filter (when not sports-only)
        if not sports_only and interests and not any(kw in title.lower() for kw in interests):
            continue

        event_slug = event.get("slug", "")

        for m in event.get("markets", []):
            outcomes = _parse(m.get("outcomes", []))
            prices   = _parse(m.get("outcomePrices", []))
            if "Yes" not in outcomes or len(prices) != 2:
                continue
            try:
                pf = [float(x) for x in prices]
            except:
                continue
            yi = outcomes.index("Yes")
            yp = pf[yi]
            if yp >= 0.97 or yp <= 0.03:
                continue

            # Build the deep link — prefer market-level slug, fall back to event slug
            market_slug  = m.get("slug", "")
            condition_id = m.get("conditionId", "")
            if event_slug and market_slug:
                poly_url = f"https://polymarket.com/event/{event_slug}/{market_slug}"
            elif event_slug:
                poly_url = f"https://polymarket.com/event/{event_slug}"
            else:
                poly_url = "https://polymarket.com"

            edge, ml, signal = None, None, "MODEL_UNAVAILABLE"
            if _model:
                feat   = np.array([[yp, 0., 0., 0.05, float(_bucket(yp)), 0.5]])
                ml     = round(float(_model.predict_proba(feat)[0][1]), 4)
                edge   = round(ml - yp, 4)
                signal = "BUY_YES" if edge > .03 else "BUY_NO" if edge < -.03 else "NEUTRAL"

            category = tags[0].get("label", "general") if tags else "general"

            out.append({
                "question":     m.get("question", title),
                "yes_price":    yp,
                "no_price":     pf[1 - yi],
                "volume":       float(m.get("volume", 0) or 0),
                "end_date":     (m.get("endDate") or "")[:10],
                "slug":         event_slug,
                "market_slug":  market_slug,
                "condition_id": condition_id,
                "poly_url":     poly_url,          # ← ready-to-use deep link
                "base_rate":    ml,
                "edge":         edge if edge is not None else (yp - 0.5),
                "signal_type":  "edge" if (edge or 0) > 0 else "value",
                "explanation":  f"Model probability: {(ml*100):.1f}%" if ml else "No model loaded — run run_pipeline.py",
                "category":     category,
                "is_sports":    is_sports_market(title, tags),
            })

    out.sort(key=lambda x: abs(x.get("edge") or 0), reverse=True)

    # Simple arb detection
    arb_groups = []
    by_event: dict = {}
    for event in events:
        eid = event.get("id")
        ms  = []
        for m in event.get("markets", []):
            outcomes = _parse(m.get("outcomes", []))
            prices   = _parse(m.get("outcomePrices", []))
            if "Yes" in outcomes and len(prices) == 2:
                try:
                    yi = outcomes.index("Yes")
                    ms.append({
                        "market_id": m.get("id"),
                        "question":  m.get("question",""),
                        "yes_price": float(prices[yi]),
                        "poly_url":  f"https://polymarket.com/event/{event.get('slug','')}/{m.get('slug','')}",
                    })
                except: pass
        if len(ms) >= 2:
            by_event[eid] = ms
    for eid, ms in by_event.items():
        total = sum(m["yes_price"] for m in ms)
        if total > 1.05:
            arb_groups.append({
                "markets":   ms,
                "total_yes": total,
                "action":    f"Sum of YES prices is {total*100:.1f}¢ — {(total-1)*100:.1f}¢ over fair. Fade overpriced legs.",
            })

    return {"markets": out[:30], "arb_groups": arb_groups[:5]}

# ── AI Mispricing Analysis ────────────────────────────────────────────────────
@app.post("/api/analyze-market")
async def analyze_market(body: dict):
    """
    Calls Gemini 2.0 Flash with Google Search grounding to detect mispricing.
    Requires GEMINI_API_KEY in .env — free tier at aistudio.google.com
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(500, "GEMINI_API_KEY not set in .env")

    question  = body.get("question", "")
    yes_price = float(body.get("yes_price", 0.5))
    poly_url  = body.get("poly_url", "")

    prompt = f"""You are a sharp sports prediction market analyst. A user wants to know if this Polymarket question is mispriced.

Market question: "{question}"
Current YES price: {yes_price:.0%}  (implies NO at {1-yes_price:.0%})

Search for current, real-world information: recent results, standings, injuries, form, sportsbook odds, and expert predictions relevant to this question.

Give your verdict in EXACTLY this format — no extra text before or after:

VERDICT: [BUY YES / BUY NO / SKIP]
FAIR VALUE: [X]%
EDGE: [+/-X%]
REASONING: [2-3 sentences — be direct and specific, cite what you found]
KEY FACTS:
• [fact 1 with source]
• [fact 2 with source]
• [fact 3 if relevant]

Only recommend BUY YES or BUY NO if the edge is greater than 5%. Otherwise say SKIP."""

    try:
        response = req.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.2},
            },
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()

        # Extract text from Gemini response structure
        try:
            parts = data["candidates"][0]["content"]["parts"]
            analysis = "\n".join(
                p["text"] for p in parts if "text" in p
            ).strip()
        except (KeyError, IndexError):
            analysis = ""

        if not analysis:
            analysis = "Analysis unavailable — Gemini returned no text. Check your GEMINI_API_KEY."

        return {"analysis": analysis, "question": question, "yes_price": yes_price, "poly_url": poly_url}

    except req.exceptions.Timeout:
        raise HTTPException(504, "AI analysis timed out — try again")
    except req.exceptions.HTTPError as e:
        detail = ""
        try: detail = e.response.json().get("error", {}).get("message", "")
        except: pass
        raise HTTPException(502, f"Gemini API error: {detail or str(e)}")
    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")

@app.get("/api/stats")
def stats():
    try:
        events = req.get(GAMMA, timeout=5).json()
        return {"open_markets": sum(len(e.get("markets",[])) for e in events), "arb_count": "Live"}
    except:
        return {"open_markets": "10k+", "arb_count": "Live"}

# ── Serve the HTML app at GET / ───────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(HTML)


# ─────────────────────────────────────────────────────────────────────────────
# THE FULL HTML APP
# ─────────────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EasyBets — Find Your Edge</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#0a0c10; --surface:#111318; --surface2:#181c24; --border:#1f2433;
  --accent:#00e676; --accent2:#00b0ff; --danger:#ff4444; --warn:#ffb300;
  --text:#e8ecf4; --muted:#5a6180;
  --mono:'DM Mono',monospace; --sans:'Syne',sans-serif;
}
html { font-size:16px; scroll-behavior:smooth; }
body { background:var(--bg); color:var(--text); font-family:var(--sans); min-height:100vh; overflow-x:hidden; }
body::before {
  content:''; position:fixed; inset:0; pointer-events:none; z-index:999; opacity:.4;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
}

.screen { display:none; min-height:100vh; animation:fadeIn .4s ease; }
.screen.active { display:flex; }
@keyframes fadeIn { from{opacity:0;transform:translateY(12px)} to{opacity:1;transform:translateY(0)} }

/* LANDING */
#screen-landing { flex-direction:column; align-items:center; justify-content:center; position:relative; padding:2rem; }
.grid-bg {
  position:absolute; inset:0;
  background-image:linear-gradient(var(--border) 1px,transparent 1px),linear-gradient(90deg,var(--border) 1px,transparent 1px);
  background-size:48px 48px;
  mask-image:radial-gradient(ellipse 80% 70% at 50% 50%,black 20%,transparent 100%);
  opacity:.4;
}
.glow-orb { position:absolute; border-radius:50%; filter:blur(120px); pointer-events:none; }
.glow-orb.green { width:500px; height:500px; background:rgba(0,230,118,.08); top:-100px; left:-100px; }
.glow-orb.blue  { width:400px; height:400px; background:rgba(0,176,255,.06); bottom:-80px; right:-80px; }
.landing-content { position:relative; z-index:1; text-align:center; max-width:640px; }
.logo-badge {
  display:inline-flex; align-items:center; gap:.5rem;
  background:var(--surface); border:1px solid var(--border); border-radius:99px;
  padding:.35rem 1rem; font-family:var(--mono); font-size:.75rem; color:var(--accent);
  letter-spacing:.08em; margin-bottom:2rem; animation:fadeIn .6s ease .1s both;
}
.logo-badge::before { content:''; width:6px; height:6px; background:var(--accent); border-radius:50%; animation:pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.4;transform:scale(.8)} }
.landing-title { font-size:clamp(3rem,8vw,5.5rem); font-weight:800; line-height:1; letter-spacing:-.03em; margin-bottom:1.5rem; animation:fadeIn .6s ease .2s both; }
.landing-title span { background:linear-gradient(135deg,var(--accent),var(--accent2)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }
.landing-sub { font-size:1.15rem; color:var(--muted); line-height:1.6; margin-bottom:2.5rem; animation:fadeIn .6s ease .3s both; }
.cta-btn {
  display:inline-flex; align-items:center; gap:.6rem;
  background:var(--accent); color:#000; font-family:var(--sans); font-weight:700;
  font-size:1rem; padding:.9rem 2.2rem; border-radius:8px; border:none; cursor:pointer;
  transition:transform .15s,box-shadow .15s; animation:fadeIn .6s ease .4s both;
}
.cta-btn:hover { transform:translateY(-2px); box-shadow:0 8px 32px rgba(0,230,118,.3); }
.landing-stats { display:flex; gap:2.5rem; justify-content:center; margin-top:3.5rem; animation:fadeIn .6s ease .5s both; }
.stat { text-align:center; }
.stat-val { font-family:var(--mono); font-size:1.6rem; font-weight:500; color:var(--text); display:block; }
.stat-label { font-size:.72rem; color:var(--muted); letter-spacing:.08em; text-transform:uppercase; }

/* CHAT */
#screen-chat { flex-direction:column; align-items:center; justify-content:flex-start; }
.chat-header {
  width:100%; padding:1.2rem 2rem; border-bottom:1px solid var(--border);
  display:flex; align-items:center; gap:1rem; background:var(--surface);
  position:sticky; top:0; z-index:10;
}
.chat-logo { font-weight:800; font-size:1.1rem; letter-spacing:-.02em; }
.chat-logo span { color:var(--accent); }
.step-indicator { margin-left:auto; font-family:var(--mono); font-size:.72rem; color:var(--muted); }
.chat-body { flex:1; width:100%; max-width:680px; margin:0 auto; padding:2rem 1.5rem; display:flex; flex-direction:column; gap:1.2rem; min-height:calc(100vh - 140px); }
.msg { display:flex; gap:.8rem; align-items:flex-start; animation:msgIn .3s ease; }
@keyframes msgIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
.msg.user { flex-direction:row-reverse; }
.avatar { width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:.8rem; flex-shrink:0; }
.avatar.ai { background:linear-gradient(135deg,var(--accent),var(--accent2)); color:#000; font-weight:700; }
.avatar.user-av { background:var(--surface2); border:1px solid var(--border); color:var(--muted); }
.bubble { max-width:75%; padding:.85rem 1.1rem; border-radius:16px; font-size:.95rem; line-height:1.55; }
.msg.ai   .bubble { background:var(--surface); border:1px solid var(--border); border-top-left-radius:4px; }
.msg.user .bubble { background:var(--accent); color:#000; font-weight:500; border-top-right-radius:4px; }
.chips-row { display:flex; flex-wrap:wrap; gap:.5rem; padding-left:42px; animation:msgIn .3s ease .15s both; }
.chip { background:var(--surface); border:1px solid var(--border); color:var(--text); padding:.45rem .9rem; border-radius:99px; font-size:.82rem; cursor:pointer; font-family:var(--sans); transition:border-color .15s,color .15s,background .15s; }
.chip:hover,.chip.selected { border-color:var(--accent); color:var(--accent); background:rgba(0,230,118,.06); }
.chat-input-row { width:100%; max-width:680px; margin:0 auto; padding:1rem 1.5rem 1.5rem; display:flex; gap:.6rem; align-items:center; }
.chat-input { flex:1; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:.8rem 1.1rem; color:var(--text); font-family:var(--sans); font-size:.9rem; outline:none; transition:border-color .15s; }
.chat-input:focus { border-color:var(--accent); }
.chat-input::placeholder { color:var(--muted); }
.send-btn { background:var(--accent); color:#000; border:none; border-radius:10px; width:42px; height:42px; display:flex; align-items:center; justify-content:center; cursor:pointer; transition:opacity .15s,transform .15s; flex-shrink:0; }
.send-btn:hover { transform:scale(1.05); }
.typing-dot { display:inline-block; width:6px; height:6px; background:var(--muted); border-radius:50%; animation:blink 1.2s infinite; }
.typing-dot:nth-child(2){animation-delay:.2s} .typing-dot:nth-child(3){animation-delay:.4s}
@keyframes blink { 0%,80%,100%{opacity:.2} 40%{opacity:1} }

/* MARKETS */
#screen-markets { flex-direction:column; align-items:stretch; }
.markets-header { padding:1.2rem 2rem; border-bottom:1px solid var(--border); background:var(--surface); display:flex; align-items:center; gap:1rem; position:sticky; top:0; z-index:10; flex-wrap:wrap; }
.markets-logo { font-weight:800; font-size:1.1rem; letter-spacing:-.02em; }
.markets-logo span { color:var(--accent); }
.profile-pill { margin-left:auto; display:flex; align-items:center; gap:.5rem; background:var(--surface2); border:1px solid var(--border); border-radius:99px; padding:.3rem .8rem .3rem .3rem; font-size:.78rem; color:var(--muted); cursor:pointer; transition:border-color .15s; }
.profile-pill:hover { border-color:var(--accent); color:var(--accent); }
.profile-dot { width:24px; height:24px; background:linear-gradient(135deg,var(--accent),var(--accent2)); border-radius:50%; }
.markets-body { max-width:900px; margin:0 auto; padding:2rem 1.5rem; width:100%; }
.section-header { display:flex; align-items:baseline; gap:.8rem; margin-bottom:1.5rem; }
.section-title { font-size:1.3rem; font-weight:700; letter-spacing:-.02em; }
.section-count { font-family:var(--mono); font-size:.72rem; color:var(--muted); background:var(--surface); border:1px solid var(--border); padding:.2rem .5rem; border-radius:4px; }
.filters { display:flex; gap:.5rem; flex-wrap:wrap; margin-bottom:1.5rem; }
.filter-chip { background:var(--surface); border:1px solid var(--border); color:var(--muted); padding:.35rem .8rem; border-radius:6px; font-size:.78rem; cursor:pointer; transition:all .15s; font-family:var(--sans); }
.filter-chip.active { background:rgba(0,230,118,.1); border-color:var(--accent); color:var(--accent); }
.market-card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:1.25rem 1.4rem; margin-bottom:.8rem; transition:border-color .2s,transform .15s; cursor:pointer; position:relative; overflow:hidden; }
.market-card::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; border-radius:3px 0 0 3px; }
.market-card.overpriced::before  { background:var(--danger); }
.market-card.underpriced::before { background:var(--accent); }
.market-card.value::before       { background:var(--warn); }
.market-card:hover { border-color:var(--muted); transform:translateY(-1px); }
.card-top { display:flex; align-items:flex-start; gap:1rem; margin-bottom:.8rem; }
.card-tag { display:inline-flex; align-items:center; gap:.3rem; padding:.2rem .55rem; border-radius:4px; font-family:var(--mono); font-size:.65rem; letter-spacing:.06em; text-transform:uppercase; flex-shrink:0; margin-top:2px; }
.card-tag.arb   { background:rgba(255,68,68,.12); color:var(--danger); border:1px solid rgba(255,68,68,.2); }
.card-tag.edge  { background:rgba(0,230,118,.1);  color:var(--accent); border:1px solid rgba(0,230,118,.2); }
.card-tag.value { background:rgba(255,179,0,.1);  color:var(--warn);   border:1px solid rgba(255,179,0,.2); }
.card-question { font-size:.98rem; font-weight:600; line-height:1.45; letter-spacing:-.01em; flex:1; }
.card-meta { display:flex; align-items:center; gap:1.2rem; flex-wrap:wrap; }
.price-block { display:flex; flex-direction:column; gap:.1rem; }
.price-label { font-family:var(--mono); font-size:.62rem; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; }
.price-val { font-family:var(--mono); font-size:1.05rem; font-weight:500; }
.price-val.yes { color:var(--accent); }
.price-val.no  { color:var(--danger); }
.edge-badge { margin-left:auto; padding:.4rem .8rem; border-radius:8px; font-family:var(--mono); font-size:.8rem; font-weight:500; }
.edge-badge.positive { background:rgba(0,230,118,.12); color:var(--accent); }
.edge-badge.negative { background:rgba(255,68,68,.12);  color:var(--danger); }
.card-explain { margin-top:.8rem; padding-top:.8rem; border-top:1px solid var(--border); font-size:.83rem; color:var(--muted); line-height:1.6; font-style:italic; display:none; }
.market-card.expanded .card-explain { display:block; }
.card-actions { margin-top:.8rem; display:none; gap:.5rem; flex-wrap:wrap; }
.market-card.expanded .card-actions { display:flex; }
.action-btn { padding:.45rem 1rem; border-radius:6px; font-size:.8rem; font-family:var(--sans); font-weight:600; cursor:pointer; border:none; transition:opacity .15s; }
.action-btn:hover { opacity:.85; }
.action-btn.yes-btn  { background:var(--accent); color:#000; }
.action-btn.no-btn   { background:var(--danger);  color:#fff; }
.action-btn.poly-btn { background:transparent; border:1px solid var(--border); color:var(--muted); }
.action-btn.ai-btn   { background:rgba(0,176,255,.12); border:1px solid rgba(0,176,255,.3); color:var(--accent2); }
.action-btn:disabled { opacity:.5; cursor:not-allowed; }
.ai-analysis { margin-top:.8rem; padding:.9rem 1rem; border-radius:8px; background:rgba(0,176,255,.06); border:1px solid rgba(0,176,255,.15); font-size:.83rem; line-height:1.65; color:var(--text); font-style:normal; white-space:pre-wrap; display:none; }
.market-card.expanded .ai-analysis.visible { display:block; }
.loading-state { text-align:center; padding:4rem 2rem; }
.spinner { width:36px; height:36px; border:2px solid var(--border); border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; margin:0 auto 1rem; }
@keyframes spin { to{transform:rotate(360deg)} }
.loading-text { font-family:var(--mono); font-size:.78rem; color:var(--muted); letter-spacing:.05em; }
.arb-group { background:var(--surface); border:1px solid rgba(255,68,68,.25); border-radius:12px; padding:1.25rem 1.4rem; margin-bottom:.8rem; }
.arb-group-title { font-size:.72rem; font-family:var(--mono); color:var(--danger); letter-spacing:.08em; text-transform:uppercase; margin-bottom:.8rem; display:flex; align-items:center; gap:.5rem; }
.arb-sum { margin-left:auto; font-size:.9rem; color:var(--text); }
.arb-leg { display:flex; align-items:center; gap:.8rem; padding:.5rem 0; border-bottom:1px solid var(--border); font-size:.85rem; }
.arb-leg:last-child { border-bottom:none; }
.arb-price { font-family:var(--mono); font-size:.85rem; color:var(--danger); flex-shrink:0; width:42px; text-align:right; }
.arb-question { flex:1; color:var(--text); }
.interest-tag { background:rgba(0,230,118,.08); border:1px solid rgba(0,230,118,.2); color:var(--accent); padding:.25rem .6rem; border-radius:99px; font-size:.72rem; font-family:var(--mono); }
@media(max-width:600px){.landing-stats{gap:1.5rem} .card-meta{gap:.8rem} .chat-body{padding:1.2rem 1rem} .markets-header{gap:.5rem}}
</style>
</head>
<body>

<!-- SCREEN 1: LANDING -->
<div id="screen-landing" class="screen active">
  <div class="grid-bg"></div>
  <div class="glow-orb green"></div>
  <div class="glow-orb blue"></div>
  <div class="landing-content">
    <div class="logo-badge">SPORTS MARKETS · AI-POWERED</div>
    <h1 class="landing-title">Find your<br><span>easy bets.</span></h1>
    <p class="landing-sub">We scan Polymarket sports markets in real time, detect mispriced outcomes using AI, and tell you exactly where the edge is — in plain English.</p>
    <button class="cta-btn" onclick="startOnboarding()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
      Find my edge
    </button>
    <div class="landing-stats">
      <div class="stat"><span class="stat-val" id="live-count">—</span><span class="stat-label">Open markets</span></div>
      <div class="stat"><span class="stat-val" id="arb-count">—</span><span class="stat-label">Arb opportunities</span></div>
      <div class="stat"><span class="stat-val">Free</span><span class="stat-label">Always</span></div>
    </div>
  </div>
</div>

<!-- SCREEN 2: CHAT -->
<div id="screen-chat" class="screen" style="flex-direction:column">
  <div class="chat-header">
    <div class="chat-logo">Easy<span>Bets</span></div>
    <div class="step-indicator" id="step-indicator">Step 1 of 3</div>
  </div>
  <div class="chat-body" id="chat-body"></div>
  <div class="chat-input-row">
    <input class="chat-input" id="chat-input" placeholder="Type your answer..." autocomplete="off" onkeydown="if(event.key==='Enter')sendMessage()"/>
    <button class="send-btn" onclick="sendMessage()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z"/></svg>
    </button>
  </div>
</div>

<!-- SCREEN 3: MARKETS -->
<div id="screen-markets" class="screen" style="flex-direction:column">
  <div class="markets-header">
    <div class="markets-logo">Easy<span>Bets</span></div>
    <div id="interest-tags" style="display:flex;gap:.4rem;flex-wrap:wrap;"></div>
    <div class="profile-pill" onclick="resetProfile()">
      <div class="profile-dot"></div>
      Change profile
    </div>
  </div>
  <div class="markets-body">
    </div>
    <div>
      <div class="section-header">
        <div class="section-title">Sports Edge Bets</div>
        <div class="section-count" id="market-count">Loading...</div>
      </div>
      <div class="filters" id="filters-row"></div>
      <div id="markets-list">
        <div class="loading-state"><div class="spinner"></div><div class="loading-text">Scanning markets...</div></div>
      </div>
    </div>
  </div>
</div>

<script>
// ─── State ────────────────────────────────────────────────────────────────────
const S = {
  session:      null,
  step:         0,
  selectedChips:[],
  allMarkets:   [],
  activeFilter: 'all',
  sportsOnly:   true,
};

// ─── Session helpers ──────────────────────────────────────────────────────────
async function initSession() {
  const sid = localStorage.getItem('ebs_sid');
  if (sid) {
    try {
      const r = await fetch('/session/' + sid);
      if (r.ok) {
        S.session = await r.json();
        if (S.session.done && S.session.profile) {
          // Returning user — skip landing and onboarding entirely
          document.getElementById('screen-landing').classList.remove('active');
          showScreen('screen-markets');
          renderInterestTags();
          loadMarkets();
          return;
        }
      }
    } catch(_) {}
  }
  // New user or expired/missing session
  showScreen('screen-landing');
  try {
    const r2 = await fetch('/session/start', { method: 'POST' });
    S.session = await r2.json();
    localStorage.setItem('ebs_sid', S.session.session_id);
  } catch(_) {}
}

async function saveProfile(profile) {
  const r = await fetch('/session/' + S.session.session_id + '/profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  });
  const data = await r.json();
  S.session.profile = data.profile;
  S.session.done    = true;
}

async function resetProfile() {
  if (!confirm('Reset your profile and redo onboarding?')) return;
  await fetch('/session/' + S.session.session_id + '/profile', { method: 'DELETE' });
  S.session.done    = false;
  S.session.profile = null;
  S.sportsOnly      = true;
  document.getElementById('chat-body').innerHTML = '';
  restartOnboarding();
}

function restartOnboarding() {
  document.getElementById('chat-body').innerHTML = '';
  showScreen('screen-chat');
  renderStep(0);
}

// ─── Screen management ────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ─── Landing ──────────────────────────────────────────────────────────────────
async function loadLandingStats() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());
    const count = d.open_markets;
    document.getElementById('live-count').textContent = typeof count === 'number' ? count.toLocaleString() : count;
    document.getElementById('arb-count').textContent  = d.arb_count || '—';
  } catch {
    document.getElementById('live-count').textContent = '10k+';
    document.getElementById('arb-count').textContent  = 'Live';
  }
}

function startOnboarding() {
  if (S.session && S.session.done && S.session.profile) {
    showScreen('screen-markets');
    renderInterestTags();
    loadMarkets();
  } else {
    showScreen('screen-chat');
    renderStep(0);
  }
}

// ─── Onboarding flow ──────────────────────────────────────────────────────────
const FLOW = [
  {
    ai: "Hey! I'm going to find prediction market bets tailored just for you. First — what topics do you actually follow? Pick everything that applies 👇",
    chips: ["⚽ Soccer / Football","🏀 NBA","🏈 NFL","⚾ MLB","🎾 Tennis","🏆 Champions League","🌍 World Cup","🥊 Boxing / MMA","⛳ Golf","🏒 NHL","🏉 Rugby","🏇 Horse Racing"],
    key:'interests', multi:true, placeholder:"Or type a sport...", step:"Step 1 of 3",
  },
  {
    ai: s => `Nice — ${(s.interests||[]).slice(0,3).join(', ')}. How aggressive do you want the picks?`,
    chips: ["🛡️ Favourites only (70%+ YES)","⚖️ Mix of both","🎯 Underdogs (big upside)"],
    key:'risk_profile', multi:false, placeholder:"Describe your style...", step:"Step 2 of 3",
  },
  {
    ai: () => "Got it. Any specific leagues, teams, or events you want focused on?",
    chips: ["Just surprise me","Champions League","NBA Playoffs","Premier League","UFC","Copa América","March Madness"],
    key:'specific', multi:false, placeholder:"e.g. 'Real Madrid' or 'surprise me'...", step:"Step 3 of 3",
  },
];

function renderStep(idx) {
  S.step = idx; S.selectedChips = [];
  const step = FLOW[idx];
  document.getElementById('step-indicator').textContent = step.step;
  const msg = typeof step.ai === 'function' ? step.ai(S.session?.profile || _profile) : step.ai;
  showTyping(() => {
    appendAI(msg);
    if (step.chips) appendChips(step.chips, step.multi, step.key);
    document.getElementById('chat-input').placeholder = step.placeholder || 'Type...';
    document.getElementById('chat-input').focus();
  });
}

function showTyping(cb) {
  const body = document.getElementById('chat-body');
  const el = document.createElement('div');
  el.className = 'msg ai'; el.id = 'typing';
  el.innerHTML = `<div class="avatar ai">E</div><div class="bubble" style="display:flex;gap:4px;align-items:center;padding:1rem 1.1rem"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>`;
  body.appendChild(el); scrollChat();
  setTimeout(() => { el.remove(); cb(); scrollChat(); }, 900);
}

function appendAI(text) {
  const body = document.getElementById('chat-body');
  const el = document.createElement('div');
  el.className = 'msg ai';
  el.innerHTML = `<div class="avatar ai">E</div><div class="bubble">${text}</div>`;
  body.appendChild(el);
}

function appendUser(text) {
  const body = document.getElementById('chat-body');
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = `<div class="bubble">${text}</div><div class="avatar user-av">You</div>`;
  body.appendChild(el); scrollChat();
}

function appendChips(options, multi, key) {
  const body = document.getElementById('chat-body');
  const row = document.createElement('div');
  row.className = 'chips-row'; row.id = 'chips-row';
  options.forEach(opt => {
    const c = document.createElement('div');
    c.className = 'chip'; c.textContent = opt;
    c.onclick = () => selectChip(c, opt, multi, key);
    row.appendChild(c);
  });
  if (multi) {
    const ok = document.createElement('div');
    ok.className = 'chip'; ok.textContent = '✓ Continue';
    ok.style.cssText = 'background:rgba(0,230,118,.1);border-color:var(--accent);color:var(--accent)';
    ok.onclick = () => { if (S.selectedChips.length) advance(key, [...S.selectedChips]); };
    row.appendChild(ok);
  }
  body.appendChild(row);
}

function selectChip(chip, value, multi, key) {
  if (!multi) {
    document.getElementById('chips-row')?.querySelectorAll('.chip').forEach(c => c.classList.remove('selected'));
    chip.classList.add('selected');
    setTimeout(() => advance(key, [value]), 300);
  } else {
    chip.classList.toggle('selected');
    S.selectedChips = chip.classList.contains('selected')
      ? [...S.selectedChips, value]
      : S.selectedChips.filter(v => v !== value);
  }
}

function sendMessage() {
  const input = document.getElementById('chat-input');
  const val = input.value.trim(); if (!val) return;
  input.value = '';
  advance(FLOW[S.step].key, [val]);
}

const _profile = {};
async function advance(key, values) {
  document.getElementById('chips-row')?.remove();
  if (key === 'interests') {
    _profile.interests = values.map(v => v.replace(/^[^\s]+\s/, '').trim());
  } else {
    _profile[key] = values[0];
  }
  appendUser(values.join(' · '));
  const next = S.step + 1;
  if (next < FLOW.length) { renderStep(next); }
  else { await finishOnboarding(); }
}

async function finishOnboarding() {
  await saveProfile(_profile);
  showTyping(() => {
    appendAI("Perfect — scanning markets now 🔍");
    scrollChat();
    setTimeout(() => { showScreen('screen-markets'); renderInterestTags(); loadMarkets(); }, 1200);
  });
}

function scrollChat() { const b = document.getElementById('chat-body'); b.scrollTop = b.scrollHeight; }



// ─── Markets ──────────────────────────────────────────────────────────────────
async function loadMarkets() {
  document.getElementById('markets-list').innerHTML = '<div class="loading-state"><div class="spinner"></div><div class="loading-text">Scanning sports markets for edges...</div></div>';
  try {
    const profile = S.session?.profile || _profile;
    const r = await fetch('/api/markets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile, sports_only: S.sportsOnly }),
    });
    const data = await r.json();
    S.allMarkets = data.markets || [];
    renderMarkets(S.allMarkets);
    if (data.arb_groups?.length) renderArbGroups(data.arb_groups);
    else document.getElementById('arb-section').style.display = 'none';
    renderFilters();
  } catch(e) {
    console.error(e);
    renderDemoData();
  }
}

function renderInterestTags() {
  const ct = document.getElementById('interest-tags'); ct.innerHTML = '';
  const interests = S.session?.profile?.interests || _profile.interests || [];
  interests.slice(0,4).forEach(i => {
    const t = document.createElement('div'); t.className = 'interest-tag'; t.textContent = i; ct.appendChild(t);
  });
}

function renderFilters() {
  const cats = ['all', ...new Set(S.allMarkets.map(m => m.category).filter(Boolean))];
  const row  = document.getElementById('filters-row'); row.innerHTML = '';
  cats.forEach(cat => {
    const c = document.createElement('div');
    c.className = `filter-chip ${cat==='all'?'active':''}`;
    c.textContent = cat === 'all' ? 'All' : cat.replace(/_/g,' ');
    c.onclick = () => {
      row.querySelectorAll('.filter-chip').forEach(x => x.classList.remove('active'));
      c.classList.add('active'); S.activeFilter = cat;
      renderMarkets(cat==='all' ? S.allMarkets : S.allMarkets.filter(m => m.category===cat));
    };
    row.appendChild(c);
  });
}

function renderMarkets(markets) {
  const list = document.getElementById('markets-list');
  document.getElementById('market-count').textContent = `${markets.length} markets`;
  if (!markets.length) {
    list.innerHTML = `<div class="loading-state"><div style="font-size:2rem;margin-bottom:1rem">🤔</div><div class="loading-text">No matching markets found.</div></div>`;
    return;
  }
  list.innerHTML = '';
  markets.forEach(m => {
    const card   = document.createElement('div');
    const eClass = (m.edge||0) > 0 ? 'positive' : 'negative';
    const cClass = m.signal_type==='arb' ? 'overpriced' : (m.edge||0)>0 ? 'underpriced' : 'value';
    const tType  = m.signal_type==='arb' ? 'arb' : (m.edge||0)>0 ? 'edge' : 'value';
    const tLabel = m.signal_type==='arb' ? '⚡ ARB' : (m.edge||0)>0 ? '📈 EDGE' : '🎯 VALUE';
    const eTxt   = m.edge != null ? `${m.edge>0?'+':''}${(m.edge*100).toFixed(1)}¢` : '—';

    // Use the pre-built poly_url from the backend
    const polyUrl = m.poly_url || 'https://polymarket.com';

    card.className = `market-card ${cClass}`;
    card.innerHTML = `
      <div class="card-top">
        <span class="card-tag ${tType}">${tLabel}</span>
        <div class="card-question">${m.question}</div>
      </div>
      <div class="card-meta">
        <div class="price-block"><span class="price-label">YES</span><span class="price-val yes">${(m.yes_price*100).toFixed(0)}¢</span></div>
        <div class="price-block"><span class="price-label">NO</span><span class="price-val no">${((1-m.yes_price)*100).toFixed(0)}¢</span></div>
        ${m.base_rate!=null?`<div class="price-block"><span class="price-label">Model</span><span class="price-val" style="color:var(--muted)">${(m.base_rate*100).toFixed(0)}%</span></div>`:''}
        <div class="edge-badge ${eClass}">${eTxt} edge</div>
      </div>
      <div class="card-explain">${m.explanation||''}</div>
      <div class="ai-analysis" id="ai-${encodeURIComponent(m.question).slice(0,30)}"></div>
      <div class="card-actions">
        <button class="action-btn yes-btn" onclick="event.stopPropagation();window.open('${polyUrl}','_blank')">Bet YES ↗</button>
        <button class="action-btn no-btn"  onclick="event.stopPropagation();window.open('${polyUrl}','_blank')">Bet NO ↗</button>
        <button class="action-btn poly-btn" onclick="event.stopPropagation();window.open('${polyUrl}','_blank')">View on Polymarket ↗</button>
        <button class="action-btn ai-btn" onclick="event.stopPropagation();analyzeMarket(this, ${JSON.stringify(m.question)}, ${m.yes_price}, '${polyUrl}')">🤖 Detect Mispricing</button>
      </div>`;
    card.onclick = () => card.classList.toggle('expanded');
    list.appendChild(card);
  });
}

// ─── AI Mispricing Analysis ───────────────────────────────────────────────────
async function analyzeMarket(btn, question, yesPrice, polyUrl) {
  const card      = btn.closest('.market-card');
  const aiBox     = card.querySelector('.ai-analysis');

  if (!card.classList.contains('expanded')) card.classList.add('expanded');

  btn.textContent = '⏳ Researching...';
  btn.disabled    = true;
  aiBox.textContent = 'Searching the web and analyzing the market...';
  aiBox.classList.add('visible');

  try {
    const r = await fetch('/api/analyze-market', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ question, yes_price: yesPrice, poly_url: polyUrl }),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: 'Unknown error' }));
      aiBox.textContent = '❌ ' + (err.detail || 'Analysis failed.');
      btn.textContent   = '🤖 Detect Mispricing';
      btn.disabled      = false;
      return;
    }

    const data       = await r.json();
    aiBox.innerHTML  = '<strong style="color:var(--accent2);font-style:normal">🤖 AI Analysis</strong><br><br>' +
                       data.analysis.replace(/\n/g, '<br>');
    btn.textContent  = '✅ Done';
  } catch(e) {
    aiBox.textContent = '❌ Network error — is the server running?';
    btn.textContent   = '🤖 Detect Mispricing';
    btn.disabled      = false;
  }
}

function renderArbGroups(groups) {
  document.getElementById('arb-section').style.display = 'block';
  document.getElementById('arb-section-count').textContent = `${groups.length} group${groups.length!==1?'s':''}`;
  const list = document.getElementById('arb-list'); list.innerHTML = '';
  groups.forEach(g => {
    const el = document.createElement('div'); el.className = 'arb-group';
    el.innerHTML = `
      <div class="arb-group-title">⚡ MUTUALLY EXCLUSIVE<span class="arb-sum">Sum: ${(g.total_yes*100).toFixed(1)}¢</span></div>
      ${g.markets.map(m=>`
        <div class="arb-leg">
          <span class="arb-price">${(m.yes_price*100).toFixed(0)}¢</span>
          <span class="arb-question">${m.question.substring(0,70)}${m.question.length>70?'…':''}</span>
          <a href="${m.poly_url||'https://polymarket.com'}" target="_blank" style="color:var(--muted);font-size:.75rem;text-decoration:none;flex-shrink:0">↗</a>
        </div>`).join('')}
      <div style="margin-top:.8rem;font-size:.82rem;color:var(--muted)">${g.action}</div>`;
    list.appendChild(el);
  });
}

function renderDemoData() {
  const demo = [
    { question:"Will the Fed cut rates at the May 2026 meeting?", yes_price:.38, edge:-.12, signal_type:'value', category:'economics', base_rate:.25, poly_url:'https://polymarket.com', explanation:"Base rate for this question type is 25% — priced at 38%, suggesting NO has edge." },
    { question:"Will Bitcoin reach $120,000 before July 2026?",   yes_price:.29, edge:-.09, signal_type:'value', category:'crypto',    base_rate:.15, poly_url:'https://polymarket.com', explanation:"Specific price targets resolve YES only ~15% historically. 29% looks overpriced." },
    { question:"Will Mbappé score in the next Champions League?", yes_price:.44, edge:.06,  signal_type:'edge',  category:'sports',    base_rate:.50, poly_url:'https://polymarket.com', explanation:"Top UCL strikers score ~50% — slightly above current 44% market price." },
  ];
  S.allMarkets = demo; renderMarkets(demo); renderFilters();
  document.getElementById('market-count').textContent = `${demo.length} markets (demo — start API)`;
}

// ─── Boot ─────────────────────────────────────────────────────────────────────
loadLandingStats();
initSession();
</script>
</body>
</html>
"""