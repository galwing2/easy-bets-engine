import requests
import pandas as pd
from datetime import datetime
import json
import time
import os

# URL for LIVE, ongoing markets
URL = "https://gamma-api.polymarket.com/markets"
FILE_PATH = "data/raw/markets.parquet"

def fetch_current_markets():
    print(f"[{datetime.utcnow()}] Fetching live market data...")
    res = requests.get(URL)
    
    if res.status_code != 200:
        print(f"Error: API returned status {res.status_code}")
        return

    data = res.json()
    rows = []
    
    for m in data:
        prices_raw = m.get("outcomePrices")
        
        if prices_raw:
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw) 
                except json.JSONDecodeError:
                    continue
            else:
                prices = prices_raw
                
            if len(prices) > 0:
                rows.append({
                    "timestamp": datetime.utcnow(),
                    "market_id": m["id"],
                    "price": float(prices[0]),
                    "volume": float(m.get("volume", 0)),
                    "liquidity": float(m.get("liquidity", 0)),
                    "end_time": pd.to_datetime(m.get("endDate"))
                })

    df = pd.DataFrame(rows)
    
    # Appends data to build a history over time
    if os.path.exists(FILE_PATH):
        existing_df = pd.read_parquet(FILE_PATH)
        combined_df = pd.concat([existing_df, df], ignore_index=True)
        combined_df.to_parquet(FILE_PATH)
        print(f"Appended {len(df)} new records. Total rows: {len(combined_df)}")
    else:
        df.to_parquet(FILE_PATH)
        print(f"Created file with {len(df)} live markets.")

if __name__ == "__main__":
    print("Starting data ingestion loop. Press Ctrl+C to stop.")
    while True:
        try:
            fetch_current_markets()
            time.sleep(300)  # Wait 5 minutes
        except KeyboardInterrupt:
            print("\nStopping data collection.")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            time.sleep(60)