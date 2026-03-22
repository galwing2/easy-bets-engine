import os
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

# 1. Load the secret connection string from the .env file
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("ERROR: MONGO_URI not found. Please check your .env file.")
    exit(1)

# 2. Connect to MongoDB Atlas
print("Connecting to MongoDB Atlas...")
client = MongoClient(MONGO_URI)
db = client["easy_bets"]         # The Database
collection = db["live_markets"]  # The Collection (Table)

# Polymarket API Endpoint (Fetching 20 active markets)
API_URL = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=20"

def fetch_and_store():
    print("Starting data ingestion loop. Press Ctrl+C to stop.")
    
    while True:
        try:
            # Use timezone-aware UTC (Fixes the previous DeprecationWarning!)
            current_time = datetime.now(timezone.utc)
            print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] Fetching live market data...")
            
            # Fetch data from Polymarket
            response = requests.get(API_URL)
            response.raise_for_status() # Check for HTTP errors
            
            market_data = response.json()
            
            # Ensure we received a valid list of markets
            if isinstance(market_data, list) and len(market_data) > 0:
                
                # Add our exact timestamp to every row before saving
                for market in market_data:
                    market["ingestion_timestamp"] = current_time
                    
                # Insert the massive batch into MongoDB instantly
                collection.insert_many(market_data)
                print(f"Successfully saved {len(market_data)} live markets to MongoDB.")
            else:
                print("Warning: Received empty or unexpected data format from Polymarket.")

            # Wait 5 minutes (300 seconds) before the next pull
            time.sleep(300)

        except Exception as e:
            print(f"Error during ingestion: {e}")
            print("Retrying in 60 seconds...")
            time.sleep(60)

if __name__ == "__main__":
    # Test the database connection before starting the engine
    try:
        client.admin.command('ping')
        print("Success! You successfully connected to MongoDB Atlas.")
        fetch_and_store()
    except Exception as e:
        print(f"MongoDB Connection Failed: {e}")
        print("Check your MONGO_URI string and ensure your IP address is whitelisted in Atlas (0.0.0.0/0).")