"""
run_pipeline.py
---------------
Orchestrates the full ML training pipeline:
  1. Load the training matrix from MongoDB (built by build_mongo_matrix.py)
  2. Engineer features (price momentum, time-bucket, price-level buckets)
  3. Train a calibrated XGBoost classifier
  4. Evaluate and save the model + calibrator to disk
  5. Print a brief performance summary

Run this script whenever you have fresh data in the training_matrix collection.
After running, the api/ server will automatically serve predictions using the
saved model files.
"""

import os
import json
import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient
from xgboost import XGBClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import train_test_split
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
from sklearn.preprocessing import LabelEncoder

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
MONGO_URI   = os.getenv("MONGO_URI")
MODEL_PATH  = "model/xgb_calibrated.joblib"
STATS_PATH  = "model/pipeline_stats.json"
MIN_ROWS_PER_MARKET = 5   # discard markets with too few price history points


# ── 1. Load raw training matrix from MongoDB ─────────────────────────────────
def load_matrix() -> pd.DataFrame:
    print("📥  Loading training matrix from MongoDB...")
    client = MongoClient(MONGO_URI)
    db     = client["easy_bets"]
    docs   = list(db["training_matrix"].find({}, {"_id": 0}))
    client.close()

    if not docs:
        raise RuntimeError("training_matrix is empty. Run build_mongo_matrix.py first.")

    df = pd.DataFrame(docs)
    print(f"    Loaded {len(df):,} rows across {df['market_id'].nunique():,} markets.")
    return df


# ── 2. Feature Engineering ───────────────────────────────────────────────────
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    print("🔧  Engineering features...")

    df = df.sort_values(["market_id", "timestamp"]).copy()

    # Within each market, compute rolling / sequential features
    grp = df.groupby("market_id")["yes_price"]

    # Price momentum: current price minus price 1 step ago
    df["price_lag1"]     = grp.shift(1)
    df["momentum_1"]     = df["yes_price"] - df["price_lag1"]

    # Price momentum over 3 steps
    df["price_lag3"]     = grp.shift(3)
    df["momentum_3"]     = df["yes_price"] - df["price_lag3"]

    # Rolling 7-step std dev (volatility proxy)
    df["volatility_7"]   = grp.transform(lambda s: s.rolling(7, min_periods=2).std())

    # Absolute price level buckets (0-10%, 10-30%, 30-70%, 70-90%, 90-100%)
    bins   = [0, 0.10, 0.30, 0.70, 0.90, 1.01]
    labels = [0, 1, 2, 3, 4]
    df["price_bucket"] = pd.cut(df["yes_price"], bins=bins, labels=labels, right=False).astype(float)

    # Relative position within market history (0 = first point, 1 = last)
    df["rel_time"] = grp.transform(lambda s: (s.index - s.index.min()) / max(len(s) - 1, 1))

    # Drop rows without enough history for lag features
    df = df.dropna(subset=["price_lag1", "price_lag3", "volatility_7"])

    # Drop markets that are too sparse after feature engineering
    counts = df.groupby("market_id").size()
    keep   = counts[counts >= MIN_ROWS_PER_MARKET].index
    df     = df[df["market_id"].isin(keep)]

    print(f"    Feature matrix: {len(df):,} rows, {df['market_id'].nunique():,} markets after filtering.")
    return df


# ── 3. Train ─────────────────────────────────────────────────────────────────
FEATURE_COLS = [
    "yes_price",
    "momentum_1",
    "momentum_3",
    "volatility_7",
    "price_bucket",
    "rel_time",
]

def train(df: pd.DataFrame):
    print("🤖  Training model...")

    X = df[FEATURE_COLS].values
    y = df["target"].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    base_model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )

    # Isotonic calibration makes probabilities match real-world hit rates
    calibrated = CalibratedClassifierCV(base_model, method="isotonic", cv=3)
    calibrated.fit(X_train, y_train)

    # ── Evaluate ─────────────────────────────────────────────────────────────
    probs = calibrated.predict_proba(X_test)[:, 1]
    ll    = log_loss(y_test, probs)
    brier = brier_score_loss(y_test, probs)
    auc   = roc_auc_score(y_test, probs)

    stats = {
        "train_rows": int(len(X_train)),
        "test_rows":  int(len(X_test)),
        "log_loss":   round(ll, 4),
        "brier_score": round(brier, 4),
        "roc_auc":    round(auc, 4),
        "feature_cols": FEATURE_COLS,
    }

    print(f"\n📊  Evaluation on held-out test set:")
    print(f"    ROC-AUC    : {auc:.4f}  (1.0 = perfect, 0.5 = random)")
    print(f"    Log Loss   : {ll:.4f}   (lower is better)")
    print(f"    Brier Score: {brier:.4f}  (lower is better, 0.25 = random)")

    return calibrated, stats


# ── 4. Save ──────────────────────────────────────────────────────────────────
def save(model, stats: dict):
    os.makedirs("model", exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\n✅  Model saved  → {MODEL_PATH}")
    print(f"    Stats saved → {STATS_PATH}")


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    raw_df      = load_matrix()
    featured_df = engineer_features(raw_df)
    model, stats = train(featured_df)
    save(model, stats)
    print("\n🚀  Pipeline complete. Start the API with:  uvicorn api.main:app --reload")