import uuid
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from config import SENDER_EMAIL, GMAIL_APP_PASSWORD, BASE_URL
from api.db import get_db
from api.models import MagicLinkRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])

def send_gmail(to_email: str, subject: str, html_content: str):
    if not SENDER_EMAIL or not GMAIL_APP_PASSWORD:
        raise Exception("Gmail credentials not configured in .env")
        
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg.add_alternative(html_content, subtype='html')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

def generate_and_send_link(email: str):
    token = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    
    get_db()["users"].update_one(
        {"email": email},
        {"$set": {"magic_token": token, "token_expires": expires}},
        upsert=True
    )
    
    # The link now points directly to the verification endpoint
    magic_link = f"{BASE_URL}/api/auth/verify?email={email}&token={token}"
    html = f"""
    <h3>Welcome to EasyBets</h3>
    <p>Click the link below to log into your account. This link will expire in 15 minutes.</p>
    <p><a href='{magic_link}'><strong>Click here to login</strong></a></p>
    """
    try:
        send_gmail(email, "Your EasyBets Login Link", html)
    except Exception as e:
        raise HTTPException(500, f"Failed to send email: {str(e)}")

@router.post("/sign-up")
def sign_up(body: MagicLinkRequest):
    email = body.email.lower().strip()
    generate_and_send_link(email)
    return {"message": "Sign up link sent"}

@router.post("/sign-in")
def sign_in(body: MagicLinkRequest):
    email = body.email.lower().strip()
    user = get_db()["users"].find_one({"email": email})
    
    if not user:
        raise HTTPException(404, "Email not found. Please Sign Up first.")
        
    generate_and_send_link(email)
    return {"message": "Sign in link sent"}

@router.get("/verify")
def verify_token(email: str, token: str):
    email = email.lower().strip()
    user = get_db()["users"].find_one({"email": email, "magic_token": token})
    
    if not user:
        # Redirect back to homepage with an error flag
        return RedirectResponse(url="/?error=invalid_token")
        
    if datetime.now(timezone.utc) > user["token_expires"].replace(tzinfo=timezone.utc):
        return RedirectResponse(url="/?error=expired_token")
        
    session_id = str(uuid.uuid4())
    
    get_db()["users"].update_one(
        {"email": email},
        {
            "$set": {"session_id": session_id},
            "$unset": {"magic_token": "", "token_expires": ""}
        }
    )
    
    # Redirect cleanly into the app, passing the session
    return RedirectResponse(url=f"/?session_id={session_id}&email={email}")