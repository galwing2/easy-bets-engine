from pydantic import BaseModel
from typing import Optional

class Profile(BaseModel):
    pass

class MarketRequest(BaseModel):
    profile: Optional[dict] = {}

class AnalyzeRequest(BaseModel):
    cache_key: str
    question: str
    yes_price: float
    market_slug: str = ""  # needed to resolve predictions later
    end_date: str = ""       # market closing date for track record display

class MagicLinkRequest(BaseModel):
    email: str

class VerifyRequest(BaseModel):
    email: str
    token: str

class AlertCreateRequest(BaseModel):
    user_email: str
    market_slug: str
    question: str
    target_price: float
    target_side: str
    target_direction: str = "below"  # "above" or "below"