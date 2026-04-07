import time
import requests
import smtplib
from email.message import EmailMessage
from api.db import get_db
from config import POLYMARKET_GAMMA, SENDER_EMAIL, GMAIL_APP_PASSWORD

def send_gmail(to_email: str, subject: str, html_content: str):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg.add_alternative(html_content, subtype='html')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)

def check_alerts():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Fetching Polymarket data...")
    try:
        events = requests.get(POLYMARKET_GAMMA, timeout=10).json()
    except Exception as e:
        print(f"Failed to fetch prices: {e}")
        return

    prices = {}
    for event in events:
        for m in event.get("markets", []):
            slug = m.get("slug")
            outcomes = m.get("outcomes", [])
            outcome_prices = m.get("outcomePrices", [])
            
            if slug and "Yes" in outcomes and len(outcome_prices) == 2:
                try:
                    yi = outcomes.index("Yes")
                    prices[slug] = float(outcome_prices[yi])
                except Exception:
                    continue

    db = get_db()
    active_alerts = list(db["alerts"].find({"fired": False}))
    
    for alert in active_alerts:
        slug = alert["market_slug"]
        if slug not in prices:
            continue
            
        current_yes = prices[slug]
        target = alert["target_price"]
        side = alert["target_side"]
        
        triggered = False
        if side == "YES" and current_yes <= target:
            triggered = True
        elif side == "NO" and (1 - current_yes) <= target:
            triggered = True
            
        if triggered:
            print(f"Triggered alert for {alert['user_email']} on {slug}")
            html = f"""
            <h3>🔔 EasyBets Alert Triggered!</h3>
            <p>Your target price of <strong>{int(target*100)}¢</strong> was reached for the market:</p>
            <p><i>{alert['question']}</i></p>
            <p><a href='https://polymarket.com'><strong>Go to Polymarket to Bet Now</strong></a></p>
            """
            try:
                send_gmail(alert["user_email"], "EasyBets Target Price Reached!", html)
                db["alerts"].update_one({"_id": alert["_id"]}, {"$set": {"fired": True}})
            except Exception as e:
                print(f"Error sending email to {alert['user_email']}: {e}")

if __name__ == "__main__":
    print("Starting background alert worker...")
    while True:
        check_alerts()
        time.sleep(300) # Runs every 5 minutes