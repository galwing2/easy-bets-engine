"""
api/models.py — Pydantic schemas for request bodies and shared types.
"""
from typing import Optional, List
from pydantic import BaseModel


class Profile(BaseModel):
    interests:    List[str]
    risk_profile: Optional[str] = "mix"
    specific:     Optional[str] = ""
    name:         Optional[str] = ""


class AnalyzeRequest(BaseModel):
    cache_key: Optional[str] = ""
    question:  str
    yes_price: float


class MarketRequest(BaseModel):
    profile:     Optional[dict] = {}
