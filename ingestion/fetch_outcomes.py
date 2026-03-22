import os
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

# 1. Load the secure connection
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("ERROR: MONGO_URI not found.")
    exit(1)

# Connect to MongoDB
client = MongoClient(MONGO_URI)
db = client["easy_bets"]
outcomes_collection = db["resolved_outcomes"] 

# Polymarket API Endpoint (Fetching CLOSED markets)
# We look for closed=true to get the historical/finished data
API_URL = "https://gamma-api.polymarket.com/markets?closed=true&limit=100"

def fetch_resolved_outcomes():
    print("Fetching closed markets to build the truth dataset...")
    
    try:
        response = requests.get(API_URL)
        response.raise_for_status()
        closed_markets = response.json()
        
        if isinstance(closed_markets, list) and len(closed_markets) > 0:
            new_inserts = 0
            
            for market in closed_markets:
                # Use the market's unique ID as our database _id to prevent duplicates
                market_id = market.get("conditionId") or market.get("id")
                
                if market_id:
                    # Upsert: Update if it exists, insert if it doesn't
                    # This ensures we don't save the same closed market twice
                    result = outcomes_collection.update_one(
                        {"_id": market_id},
                        {"$set": market},
                        upsert=True
                    )
                    
                    if result.upserted_id:
                        new_inserts += 1

            print(f"Successfully added {new_inserts} new resolved outcomes to the database.")
            print(f"Total closed markets processed: {len(closed_markets)}")
            
        else:
            print("No closed markets found or unexpected data format.")

    except Exception as e:
        print(f"Error fetching outcomes: {e}")

if __name__ == "__main__":
    fetch_resolved_outcomes()