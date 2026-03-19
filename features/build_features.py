import pandas as pd
import os

def build_features():
    print("1. Loading raw datasets...")
    df = pd.read_parquet("data/raw/markets.parquet")
    outcomes = pd.read_parquet("data/raw/outcomes.parquet")

    print("2. Merging datasets...")
    df = df.merge(outcomes, on="market_id", how="left")
    
    df = df.sort_values(["market_id", "timestamp"])

    print("3. Engineering features...")
    
    df["momentum_1"] = df.groupby("market_id")["price"].pct_change(1)
    df["momentum_5"] = df.groupby("market_id")["price"].pct_change(5)
    df["volatility_10"] = df.groupby("market_id")["price"].rolling(10).std().reset_index(0, drop=True)
    df["volume_spike"] = df["volume"] / df.groupby("market_id")["volume"].rolling(20).mean().reset_index(0, drop=True)

    # Timezone fix
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["end_time"] = pd.to_datetime(df["end_time"], utc=True)

    df["time_to_end"] = (df["end_time"] - df["timestamp"]).dt.total_seconds()
    df["urgency"] = 1 / (df["time_to_end"] + 1)

    print(f"Rows before cleaning: {len(df)}")
    
    # Temporarily commented out so you can inspect the data while the scraper builds history
    # df = df.dropna() 
    
    print(f"Rows after dropping NaNs: {len(df)}")
    
    print("\nFeature Preview:")
    print(df[['market_id', 'price', 'momentum_1', 'volume_spike', 'urgency']].head(10))
    
    df.to_parquet("data/processed/features.parquet")
    print("Success! ML dataset saved to data/processed/features.parquet")

if __name__ == "__main__":
    os.makedirs("data/processed", exist_ok=True)
    build_features()