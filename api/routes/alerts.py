from bson.objectid import ObjectId
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from api.db import get_db
from api.models import AlertCreateRequest
from config import MAX_ALERTS_PER_USER

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

@router.get("/{email}")
def get_alerts(email: str):
    alerts = list(get_db()["alerts"].find({"user_email": email, "fired": False}))
    for a in alerts:
        a["_id"] = str(a["_id"])
    return {"alerts": alerts}

@router.post("/")
def create_alert(body: AlertCreateRequest):
    db = get_db()

    active_count = db["alerts"].count_documents({"user_email": body.user_email, "fired": False})
    if active_count >= MAX_ALERTS_PER_USER:
        raise HTTPException(400, f"Limit reached. You can only have {MAX_ALERTS_PER_USER} active alerts.")

    # Sanitise direction — fall back to "below" if anything unexpected arrives
    direction = body.target_direction if body.target_direction in ("above", "below") else "below"

    alert = {
        "user_email":       body.user_email,
        "market_slug":      body.market_slug,
        "question":         body.question,
        "target_price":     body.target_price,
        "target_side":      body.target_side,
        "target_direction": direction,
        "fired":            False,
        "created_at":       datetime.now(timezone.utc).isoformat()
    }

    db["alerts"].insert_one(alert)
    return {"message": "Alert created successfully"}

@router.delete("/{alert_id}")
def delete_alert(alert_id: str):
    db = get_db()
    result = db["alerts"].delete_one({"_id": ObjectId(alert_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, "Alert not found")
    return {"message": "Alert removed successfully"}