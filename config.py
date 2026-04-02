"""
config.py — single source of truth for all settings.
Import this everywhere instead of scattering os.getenv() calls.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── External APIs ─────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL:   str = "gemini-2.5-flash"
GEMINI_URL:     str = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

POLYMARKET_GAMMA: str = (
    "https://gamma-api.polymarket.com/events"
    "?closed=false&limit=1000&order=volume&ascending=false"
)

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI:  str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB:   str = "easy_bets"

# ── ML ────────────────────────────────────────────────────────────────────────
MODEL_PATH: str = os.getenv("MODEL_PATH", "model/xgb_calibrated.joblib")

# ── Cache ─────────────────────────────────────────────────────────────────────
ANALYSIS_CACHE_TTL_HOURS: int = 6

# ── CORS / Server ─────────────────────────────────────────────────────────────
ALLOWED_ORIGINS: list = ["*"]
