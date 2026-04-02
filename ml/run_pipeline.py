"""
ml/run_pipeline.py
------------------
Full ML training pipeline:
  1. Load training matrix from MongoDB
  2. Engineer features
  3. Train a calibrated XGBoost classifier
  4. Evaluate + save model to model/

Run: python -m ml.run_pipeline
"""

import sys
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

sys.path.insert(0, ".")
load_dotenv()

from config import MONGO_URI, MONGO_DB, MODEL_PATH

STATS_PATH          = MODEL_PATH.replace(".joblib", "_stats.json")
MIN_ROWS_PER_MARKET = 5

FEATURE_COLS = [
    "yes_price",
    "momentum_1",
    "momentum_3",
    "volatility_7",
    "price_bucket",
    "rel_time",
]


# ── 1. Load ───────────────────────────────────────────────────────────────────
def load_matrix() -> pd.DataFrame:
    print("📥  Loading training matrix from MongoDB...")
    client = MongoClient(MONGO_URI)
    docs   = list(client[MONGO_DB]["training_matrix"].find({}, {"_id": 0}))
    client.close()

    if not docs:
        raise RuntimeError("training_matrix is empty. Run ingestion/build_mongo_matrix.py first.")

    df = pd.DataFrame(docs)
    print(f"    Loaded {len(df):,} rows across {df['market_id'].nunique():,} markets.")
    return df


# ── 2. Feature engineering ────────────────────────────────────────────────────
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    print("🔧  Engineering features...")
    df  = df.sort_values(["market_id", "timestamp"]).copy()
    grp = df.groupby("market_id")["yes_price"]

    df["price_lag1"]   = grp.shift(1)
    df["momentum_1"]   = df["yes_price"] - df["price_lag1"]
    df["price_lag3"]   = grp.shift(3)
    df["momentum_3"]   = df["yes_price"] - df["price_lag3"]
    df["volatility_7"] = grp.transform(lambda s: s.rolling(7, min_periods=2).std())

    bins   = [0, 0.10, 0.30, 0.70, 0.90, 1.01]
    labels = [0, 1, 2, 3, 4]
    df["price_bucket"] = pd.cut(
        df["yes_price"], bins=bins, labels=labels, right=False
    ).astype(float)

    df["rel_time"] = grp.transform(
        lambda s: (s.index - s.index.min()) / max(len(s) - 1, 1)
    )

    df = df.dropna(subset=["price_lag1", "price_lag3", "volatility_7"])

    counts = df.groupby("market_id").size()
    keep   = counts[counts >= MIN_ROWS_PER_MARKET].index
    df     = df[df["market_id"].isin(keep)]

    print(f"    {len(df):,} rows, {df['market_id'].nunique():,} markets after filtering.")
    return df


# ── 3. Train ──────────────────────────────────────────────────────────────────
def train(df: pd.DataFrame):
    print("🤖  Training model...")
    X = df[FEATURE_COLS].values
    y = df["target"].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    base = XGBClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        use_label_encoder=False, eval_metric="logloss",
        random_state=42, n_jobs=-1,
    )
    model = CalibratedClassifierCV(base, method="isotonic", cv=3)
    model.fit(X_train, y_train)

    probs = model.predict_proba(X_test)[:, 1]
    stats = {
        "train_rows":   int(len(X_train)),
        "test_rows":    int(len(X_test)),
        "log_loss":     round(log_loss(y_test, probs), 4),
        "brier_score":  round(brier_score_loss(y_test, probs), 4),
        "roc_auc":      round(roc_auc_score(y_test, probs), 4),
        "feature_cols": FEATURE_COLS,
    }

    print(f"\n📊  Test set metrics:")
    print(f"    ROC-AUC    : {stats['roc_auc']}")
    print(f"    Log Loss   : {stats['log_loss']}")
    print(f"    Brier Score: {stats['brier_score']}")
    return model, stats


# ── 4. Save ───────────────────────────────────────────────────────────────────
def save(model, stats: dict):
    os.makedirs(os.path.dirname(MODEL_PATH) or ".", exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\n✅  Model  → {MODEL_PATH}")
    print(f"    Stats  → {STATS_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    raw_df      = load_matrix()
    featured_df = engineer_features(raw_df)
    model, stats = train(featured_df)
    save(model, stats)
    print("\n🚀  Pipeline complete. Start API with:  uvicorn api.main:app --reload")
