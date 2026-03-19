import requests
import pandas as pd
import json

# URL specifically asking only for CLOSED, finished markets
URL = "https://gamma-api.polymarket.com/markets?closed=true"

def fetch_market_outcomes():
    print("Fetching closed markets for answer labels...")
    res = requests.get(URL)
    
    if res.status_code != 200:
        print(f"Error: API returned status {res.status_code}")
        return

    data = res.json()
    rows = []
    
    for m in data:
        prices_raw = m.get("outcomePrices")
        
        # We have to parse the stringified lists just like we did in fetch_markets
        if prices_raw:
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw) 
                except json.JSONDecodeError:
                    continue
            else:
                prices = prices_raw
            
            if len(prices) > 0:
                yes_price = float(prices[0])
                
                # If the market is resolved, the YES price will be ~1.0 or ~0.0
                if yes_price > 0.95:
                    rows.append({"market_id": m["id"], "label": 1})
                elif yes_price < 0.05:
                    rows.append({"market_id": m["id"], "label": 0})

    df = pd.DataFrame(rows)
    
    if len(df) > 0:
        df.to_parquet("data/raw/outcomes.parquet")
        print(f"Success! Saved {len(df)} final outcome labels to data/raw/outcomes.parquet")
    else:
        print("Still 0 rows. We might need to look closer at the API response!")

if __name__ == "__main__":
    fetch_market_outcomes()