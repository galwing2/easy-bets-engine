import os
from dotenv import load_dotenv

load_dotenv()

# External APIs
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = "gemini-2.5-flash"
GEMINI_URL: str = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
POLYMARKET_GAMMA: str = "https://gamma-api.polymarket.com/events?closed=false&limit=1000&order=volume&ascending=false"

# Email Configuration (Gmail SMTP)
SENDER_EMAIL: str = os.getenv("SENDER_EMAIL", "")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")
BASE_URL: str = os.getenv("BASE_URL", "http://13.60.66.250:8000")

# Limits
MAX_ALERTS_PER_USER: int = 5

# MongoDB
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB: str = "easy_bets"

# Cache & Paths
MODEL_PATH: str = os.getenv("MODEL_PATH", "model/xgb_calibrated.joblib")
ANALYSIS_CACHE_TTL_HOURS: int = 6
ALLOWED_ORIGINS: list = ["*"]