import os
import requests
import json
import time
from pymongo import MongoClient
from dotenv import load_dotenv

# 1. Connect to your Cloud Vault
load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
db = client["easy_bets"]

def build_modern_dataset():
    print("Phase 1: Fetching the highest-volume modern markets...")
    url = "https://gamma-api.polymarket.com/events?closed=true&limit=100&offset=0&order=volume&ascending=false"
    resp = requests.get(url)
    events = resp.json()
    
    matrix_rows = []
    print(f"Found {len(events)} massive events. Extracting daily price histories...")
    
    for i, event in enumerate(events):
        markets = event.get("markets", [])
        for market in markets:
            outcomes = market.get("outcomes", [])
            
            # We only want clean, binary Yes/No markets
            if "Yes" not in outcomes or "No" not in outcomes:
                continue
                
            prices = market.get("outcomePrices", [])
            try:
                prices_float = [float(p) for p in prices]
            except:
                continue
            
            # Identify if it clearly resolved to 1 (Yes) or 0 (No)
            if prices_float == [1.0, 0.0] or prices_float == [0.0, 1.0]:
                yes_index = outcomes.index("Yes")
                target = 1 if prices_float[yes_index] == 1.0 else 0
            else:
                continue
                
            try:
                clob_ids = json.loads(market.get("clobTokenIds", "[]"))
                yes_token_id = clob_ids[yes_index]
            except:
                continue
                
            # Fetch price history from the CLOB API
            history_url = f"https://clob.polymarket.com/prices-history?market={yes_token_id}&interval=max"
            
            try:
                h_resp = requests.get(history_url, timeout=5)
                history = h_resp.json().get('history', [])
                
                # Fallback if 'max' interval is rejected
                if not history:
                    history_url = f"https://clob.polymarket.com/prices-history?market={yes_token_id}&fidelity=1440"
                    h_resp = requests.get(history_url, timeout=5)
                    history = h_resp.json().get('history', [])

                for point in history:
                    matrix_rows.append({
                        "market_id": market.get("id"),
                        "timestamp": point['t'],
                        "yes_price": point['p'],
                        "target": target
                    })
            except:
                pass
            
            time.sleep(0.1) 
            
        if (i + 1) % 10 == 0:
            print(f"Processed {i + 1} events... (Generated {len(matrix_rows)} training rows so far)")

    # 2. Save directly to MongoDB
    if matrix_rows:
        print(f"\n--- MATRIX COMPLETE: {len(matrix_rows)} rows ---")
        print("Clearing out any old training data...")
        db["training_matrix"].drop() # Wipe the old slate clean
        
        print("Uploading fresh Matrix to MongoDB Atlas...")
        # Insert the massive list of dictionaries directly into the cloud
        db["training_matrix"].insert_many(matrix_rows)
        print("Success! Your AI training data is securely locked in the vault.")
    else:
        print("Error: No rows generated.")

if __name__ == "__main__":
    build_modern_dataset()