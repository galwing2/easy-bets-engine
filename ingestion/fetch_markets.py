import os
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("ERROR: MONGO_URI not found.")
    exit(1)

client = MongoClient(MONGO_URI)
db = client["easy_bets"]         
collection = db["live_markets"]  

def fetch_and_store():
    print("Starting high-volume data ingestion. Press Ctrl+C to stop.")
    
    while True:
        try:
            current_time = datetime.now(timezone.utc)
            print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] Paginating API for Top 300 Events...")
            
            all_market_data = []
            
            for offset in [0, 100, 200]:
                url = f"https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100&offset={offset}&order=volume&ascending=false"                
                response = requests.get(url)
                response.raise_for_status()
                
                batch = response.json()
                
                if isinstance(batch, list) and len(batch) > 0:
                    for event in batch:
                        if "markets" in event:
                            all_market_data.extend(event["markets"])
                else:
                    break
                    
            # THE FIX: using all_market_data instead of all_markets
            if all_market_data:
                db.live_markets.drop() 
                db.live_markets.insert_many(all_market_data) 
                print(f"Success! Live Cache updated with {len(all_market_data)} markets.") 
            else:
                print("Warning: Received no data from Polymarket.")

            time.sleep(300)

        except Exception as e:
            print(f"Error during ingestion: {e}")
            print("Retrying in 60 seconds...")
            time.sleep(60)

if __name__ == "__main__":
    try:
        client.admin.command('ping')
        fetch_and_store()
    except Exception as e:
        print(f"MongoDB Connection Failed: {e}")