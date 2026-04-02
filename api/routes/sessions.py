"""
api/routes/sessions.py — session CRUD endpoints.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pymongo import ReturnDocument

from api.db import get_db
from api.models import Profile

router = APIRouter(prefix="/session", tags=["sessions"])


@router.post("/start")
def session_start():
    sid = str(uuid.uuid4())
    get_db()["sessions"].insert_one({
        "session_id": sid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile":    None,
        "done":       False,
    })
    return {"session_id": sid}


@router.get("/{sid}")
def session_get(sid: str):
    doc = get_db()["sessions"].find_one({"session_id": sid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "not found")
    return doc


@router.post("/{sid}/profile")
def session_save(sid: str, p: Profile):
    doc = get_db()["sessions"].find_one_and_update(
        {"session_id": sid},
        {"$set": {
            "profile":    p.model_dump(),
            "done":       True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        raise HTTPException(404, "not found")
    return {"session_id": sid, "profile": doc["profile"]}


@router.delete("/{sid}/profile")
def session_reset(sid: str):
    get_db()["sessions"].update_one(
        {"session_id": sid},
        {"$set": {"profile": None, "done": False}},
    )
    return {"reset": True}
