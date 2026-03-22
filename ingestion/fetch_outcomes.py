import os
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("ERROR: MONGO_URI not found. Please check your .env file.")
    exit(1)

client = MongoClient(MONGO_URI)
db = client["easy_bets"]
outcomes_collection = db["resolved_outcomes"] 

def fetch_resolved_outcomes():
    print("Paginating API to fetch a massive batch of resolved outcomes...")
    
    total_inserted = 0
    
    # The Pagination Loop: Grabbing 5 pages of 100 markets each
    for offset in [0, 100, 200, 300, 400]:
        print(f"Fetching closed markets (offset {offset})...")
        url = f"https://gamma-api.polymarket.com/markets?closed=true&limit=100&offset={offset}"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            closed_markets = response.json()
            
            if isinstance(closed_markets, list) and len(closed_markets) > 0:
                for market in closed_markets:
                    # Use the market's unique ID as our database _id to prevent duplicates
                    market_id = market.get("conditionId") or market.get("id")
                    
                    if market_id:
                        # Upsert: Update if it exists, insert if it doesn't
                        result = outcomes_collection.update_one(
                            {"_id": market_id},
                            {"$set": market},
                            upsert=True
                        )
                        
                        if result.upserted_id:
                            total_inserted += 1
            else:
                break # Stop if we run out of historical data
                
        except Exception as e:
            print(f"Error on offset {offset}: {e}")

    print(f"Success! Added {total_inserted} brand new resolved outcomes to the database.")

if __name__ == "__main__":
    fetch_resolved_outcomes()