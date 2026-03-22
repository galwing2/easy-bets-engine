import os
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("ERROR: MONGO_URI not found. Please check your .env file.")
    exit(1)

client = MongoClient(MONGO_URI)
db = client["easy_bets"]         
collection = db["live_markets"]  

def fetch_and_store():
    print("Starting high-volume data ingestion. Press Ctrl+C to stop.")
    
    while True:
        try:
            current_time = datetime.now(timezone.utc)
            print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] Paginating API for Top 300 markets...")
            
            all_market_data = []
            
            # The Pagination Loop: Page 1 (offset 0), Page 2 (offset 100), Page 3 (offset 200)
            for offset in [0, 100, 200]:
                # Notice we added limit=100, offset, and order=volume_24hr
                url = f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset={offset}&order=volume_24hr&ascending=false"
                
                response = requests.get(url)
                response.raise_for_status()
                
                batch = response.json()
                
                if isinstance(batch, list) and len(batch) > 0:
                    all_market_data.extend(batch)
                else:
                    break # Stop if we run out of markets
                    
            if len(all_market_data) > 0:
                # Stamp the whole batch with the exact same timestamp
                for market in all_market_data:
                    market["ingestion_timestamp"] = current_time
                    
                collection.insert_many(all_market_data)
                print(f"Success! Saved {len(all_market_data)} high-volume markets to MongoDB.")
            else:
                print("Warning: Received no data from Polymarket.")

            # Wait 5 minutes before the next sweep
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