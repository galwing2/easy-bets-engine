"""
api/db.py — MongoDB connection singleton.
Import get_db() wherever you need a collection.
"""
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if not _client:
        _client = MongoClient(MONGO_URI)
    return _client


def get_db():
    return get_client()[MONGO_DB]
