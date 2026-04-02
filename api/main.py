"""
api/main.py — FastAPI entrypoint.
Wires together routers and serves the frontend as static files.

Run: uvicorn api.main:app --reload
"""
import os
import joblib
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from config import ALLOWED_ORIGINS, MODEL_PATH
from api.routes import sessions, markets, analysis

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="EasyBets API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ML model startup ──────────────────────────────────────────────────────────
@app.on_event("startup")
def load_model():
    if os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)
        markets.set_model(model)
        print(f"✅ Model loaded from {MODEL_PATH}")
    else:
        print(f"⚠️  No model found at {MODEL_PATH} — ML scoring disabled")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(sessions.router)
app.include_router(markets.router)
app.include_router(analysis.router)

# ── Static files (CSS, JS) ────────────────────────────────────────────────────
FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND / "static")), name="static")

# ── Serve index.html for all non-API routes ───────────────────────────────────
INDEX = (FRONTEND / "templates" / "index.html").read_text()

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(INDEX)
