"""
alert_worker.py
---------------------
Background worker that runs continuously, checks Polymarket for live prices,
and sends an email to the user when their price alert target is reached.

Run standalone: python alert_worker.py

Trigger logic:
  Alerts fire when the live price DROPS TO or BELOW the user's target.
  e.g. User sets YES @ 40¢ → fires when live YES price is 40¢ or lower.
  e.g. User sets NO  @ 30¢ → fires when live NO  price is 30¢ or lower.
  This is the standard "buy limit" model: you want to enter at a good price.
"""

import time
import json
import requests
import smtplib
from email.message import EmailMessage
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

from api.db import get_db

# Constants
GAMMA_SINGLE = "https://gamma-api.polymarket.com/markets?slug={slug}"
CHECK_DELAY  = 0.5   # seconds between API calls per slug
LOOP_DELAY   = 300   # seconds between full scans (5 minutes)

SENDER_EMAIL       = os.getenv("SENDER_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def send_alert_email(to_email: str, question: str, target_side: str, target_price: float, live_price: float, slug: str):
    """Sends an HTML email notifying the user their alert triggered."""
    if not SENDER_EMAIL or not GMAIL_APP_PASSWORD:
        print("⚠️  Warning: Gmail credentials missing. Cannot send email.")
        return

    poly_url = f"https://polymarket.com/event/{slug}"
    subject  = f"🔔 EasyBets Alert: {target_side} hit {(target_price * 100):.0f}¢!"

    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 10px;">
        <h2 style="color: #00e676; margin-top: 0;">⚡ EasyBets Alert Triggered</h2>
        <p style="font-size: 16px; color: #333;">Your target price has been reached on Polymarket!</p>
        
        <div style="background-color: #f5f5f5; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 0 0 10px 0; font-weight: bold; color: #111;">{question}</p>
            <p style="margin: 0; color: #555;">Target: <strong>{target_side} @ {(target_price * 100):.0f}¢</strong></p>
            <p style="margin: 5px 0 0 0; color: #555;">Current Live Price: <strong style="color: #00e676;">{(live_price * 100):.0f}¢</strong></p>
        </div>
        
        <a href="{poly_url}" style="display: inline-block; background-color: #000; color: #fff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: bold;">View on Polymarket &rarr;</a>
        
        <p style="margin-top: 30px; font-size: 12px; color: #999;">This alert has now been removed from your account, freeing up your quota.</p>
    </div>
    """

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From']    = f"EasyBets Alerts <{SENDER_EMAIL}>"
    msg['To']      = to_email
    msg.add_alternative(html_content, subtype='html')

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        print(f"  ✉️  Email sent successfully to {to_email}")
    except Exception as e:
        print(f"  ⚠️  Failed to send email to {to_email}: {e}")


def check_alerts():
    db = get_db()
    active_alerts = list(db["alerts"].find({"fired": False}))

    if not active_alerts:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No active alerts to check.")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {len(active_alerts)} active alerts...")

    # Group by slug so we only hit the API once per market
    alerts_by_slug = {}
    for alert in active_alerts:
        slug = alert.get("market_slug")
        if slug:
            alerts_by_slug.setdefault(slug, []).append(alert)

    for slug, alerts in alerts_by_slug.items():
        try:
            resp = requests.get(GAMMA_SINGLE.format(slug=slug), timeout=10)
            if not resp.ok:
                time.sleep(CHECK_DELAY)
                continue

            market_data = resp.json()
            if not market_data:
                time.sleep(CHECK_DELAY)
                continue

            market   = market_data[0] if isinstance(market_data, list) else market_data
            outcomes = market.get("outcomes", "[]")
            prices   = market.get("outcomePrices", "[]")

            if isinstance(outcomes, str): outcomes = json.loads(outcomes)
            if isinstance(prices,   str): prices   = json.loads(prices)

            if "Yes" not in outcomes or len(prices) != 2:
                time.sleep(CHECK_DELAY)
                continue

            yes_idx = outcomes.index("Yes")
            no_idx  = 1 - yes_idx

            live_yes = float(prices[yes_idx])
            live_no  = float(prices[no_idx])

            for alert in alerts:
                target_side  = alert["target_side"].upper()
                target_price = float(alert["target_price"])

                # Fire when the live price has DROPPED TO or BELOW the target.
                # This is a standard buy-limit alert: the user wants to be notified
                # when they can buy at their desired price or better (cheaper).
                if target_side == "YES":
                    triggered     = live_yes <= target_price
                    trigger_price = live_yes
                elif target_side == "NO":
                    triggered     = live_no <= target_price
                    trigger_price = live_no
                else:
                    continue

                if triggered:
                    print(f"  🚨 ALERT TRIGGERED: {alert['user_email']} | {target_side} @ {target_price*100:.0f}¢ (Live: {trigger_price*100:.0f}¢)")
                    send_alert_email(
                        to_email=alert["user_email"],
                        question=alert["question"],
                        target_side=target_side,
                        target_price=target_price,
                        live_price=trigger_price,
                        slug=slug,
                    )
                    db["alerts"].delete_one({"_id": alert["_id"]})

        except Exception as e:
            print(f"  ⚠️  Error checking slug {slug}: {e}")

        time.sleep(CHECK_DELAY)


if __name__ == "__main__":
    print("Starting EasyBets Alert Worker...")
    while True:
        check_alerts()
        time.sleep(LOOP_DELAY)