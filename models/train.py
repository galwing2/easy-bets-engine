import pandas as pd
from xgboost import XGBClassifier
import os

def train_model():
    print("1. Loading feature dataset...")
    if not os.path.exists("data/processed/features.parquet"):
        return

    df = pd.read_parquet("data/processed/features.parquet")

    # --- THE ARCHITECTURE FIX ---
    # 1. Historical Data (Has an answer key. We use this to train.)
    historical_data = df[df["label"].notna()]
    
    # 2. Live Data (No answer key, currently trading. We use this to predict.)
    # We apply the "live market" filter here so we only bet on active markets.
    live_data = df[df["label"].isna() & (df["price"] > 0.02) & (df["price"] < 0.98)]

    if len(historical_data) == 0 or len(live_data) == 0:
        print("Error: Not enough data. Let scraper run longer.")
        return

    features = ["momentum_1", "momentum_5", "volatility_10", "volume_spike", "urgency"]

    print(f"2. Training on {len(historical_data)} historical rows...")
    X_train, y_train = historical_data[features], historical_data["label"]
    
    # Train the model (Note: Until you collect weeks of data, this model is a "dummy" 
    # because it is only learning from dead markets right now, but the mechanics will work!)
    model = XGBClassifier(n_estimators=100, max_depth=5, random_state=42)
    model.fit(X_train, y_train)
    
    print(f"Training Accuracy: {model.score(X_train, y_train):.2%}")

    # --- DAY 9 & 10: LIVE PREDICTIONS & EDGE ---
    print(f"\n3. Scanning {len(live_data)} LIVE active rows for betting edge...")
    
    predictions = live_data.copy()
    predictions["pred_prob"] = model.predict_proba(live_data[features])[:, 1]
    predictions["edge"] = predictions["pred_prob"] - predictions["price"]

    # SMART BETTING RULE: Edge > 10%
    predictions["bet"] = predictions["edge"] > 0.01

    predictions.to_parquet("data/processed/predictions.parquet")
    
    bets_found = predictions["bet"].sum()
    print(f"Success! Found {bets_found} viable betting opportunities.")
    
    if bets_found > 0:
        print("\nPreview of the top betting signals:")
        top_bets = predictions[predictions["bet"] == True][["market_id", "price", "pred_prob", "edge"]]
        print(top_bets.sort_values(by="edge", ascending=False).head())

if __name__ == "__main__":
    train_model()