"""
alert_worker.py — EasyBets background price alert worker.

Run standalone:  python alert_worker.py

Trigger logic:
  target_direction "below" -> fire when live price <= target  (buy the dip)
  target_direction "above" -> fire when live price >= target  (momentum entry)
  Old alerts without target_direction default to "below".
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

GAMMA_SINGLE       = "https://gamma-api.polymarket.com/markets?slug={slug}" 
CHECK_DELAY        = 0.5 # Minimum delay between API calls to avoid rate limits (in seconds)
LOOP_DELAY         = 120  # Minimum delay between alert checks to avoid hammering the API (in seconds)
SENDER_EMAIL       = os.getenv("SENDER_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def send_alert_email(to_email, question, target_side, target_price, target_direction, live_price, slug):
    if not SENDER_EMAIL or not GMAIL_APP_PASSWORD:
        print("  Warning: Gmail credentials missing.")
        return

    poly_url  = "https://polymarket.com/event/{}".format(slug)
    dir_label = "dropped to or below" if target_direction == "below" else "risen to or above"
    subject   = "EasyBets Alert: {} has {} {:.0f}c".format(target_side, dir_label, target_price * 100)

    # Note if price moved further past the target during the 5-min polling window
    past_target = (
        (target_direction == "below" and live_price < target_price) or
        (target_direction == "above" and live_price > target_price)
    )
    live_note = " (moved further to {:.0f}c in the last 5 min)".format(live_price * 100) if past_target else ""

    html = """
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;
                border:1px solid #e0e0e0;border-radius:10px;">
      <h2 style="color:#00e676;margin-top:0;">EasyBets Alert Triggered</h2>
      <p style="font-size:16px;color:#333;">Your price alert has been reached on Polymarket!</p>
      <div style="background:#f5f5f5;padding:15px;border-radius:8px;margin:20px 0;">
        <p style="margin:0 0 10px 0;font-weight:bold;color:#111;">{question}</p>
        <p style="margin:0;color:#555;">
          Your alert: <strong>{side} {dir_label} {target_c:.0f}c</strong>
        </p>
        <p style="margin:5px 0 0 0;color:#555;">
          Price when checked: <strong style="color:#00e676;">{live_c:.0f}c</strong>{live_note}
        </p>
        <p style="margin:8px 0 0 0;font-size:12px;color:#999;">
          Alerts are checked every 5 minutes so the live price may differ slightly from your exact target.
        </p>
      </div>
      <a href="{poly_url}" style="display:inline-block;background:#000;color:#fff;
         text-decoration:none;padding:12px 24px;border-radius:6px;font-weight:bold;">
        View on Polymarket
      </a>
      <p style="margin-top:30px;font-size:12px;color:#999;">
        This alert has been removed from your account.
      </p>
    </div>
    """.format(
        question=question,
        side=target_side,
        dir_label=dir_label,
        target_c=target_price * 100,
        live_c=live_price * 100,
        live_note=live_note,
        poly_url=poly_url,
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = "EasyBets Alerts <{}>".format(SENDER_EMAIL)
    msg["To"]      = to_email
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        print("  Email sent to {}".format(to_email))
    except Exception as e:
        print("  Failed to send email to {}: {}".format(to_email, e))


def check_alerts():
    db            = get_db()
    active_alerts = list(db["alerts"].find({"fired": False}))

    if not active_alerts:
        print("[{}] No active alerts.".format(datetime.now().strftime("%H:%M:%S")))
        return

    print("[{}] Checking {} alerts...".format(datetime.now().strftime("%H:%M:%S"), len(active_alerts)))

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

            yes_idx  = outcomes.index("Yes")
            live_yes = float(prices[yes_idx])
            live_no  = float(prices[1 - yes_idx])

            for alert in alerts:
                target_side      = alert["target_side"].upper()
                target_price     = float(alert["target_price"])
                target_direction = alert.get("target_direction", "below")

                live_price = live_yes if target_side == "YES" else live_no

                triggered = (
                    live_price <= target_price if target_direction == "below"
                    else live_price >= target_price
                )

                if triggered:
                    print("  TRIGGERED: {} | {} {} {:.0f}c (live: {:.0f}c)".format(
                        alert["user_email"], target_side, target_direction,
                        target_price * 100, live_price * 100
                    ))
                    send_alert_email(
                        to_email=alert["user_email"],
                        question=alert["question"],
                        target_side=target_side,
                        target_price=target_price,
                        target_direction=target_direction,
                        live_price=live_price,
                        slug=slug,
                    )
                    db["alerts"].delete_one({"_id": alert["_id"]})

        except Exception as e:
            print("  Error checking {}: {}".format(slug, e))

        time.sleep(CHECK_DELAY)


if __name__ == "__main__":
    print("Starting EasyBets Alert Worker...")
    while True:
        check_alerts()
        time.sleep(LOOP_DELAY)