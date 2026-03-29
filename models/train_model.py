"""
train_model.py
--------------
Pulls the raw price-history matrix from MongoDB, engineers features
per market, trains an XGBoost classifier, and saves the model + scaler.

Run:  python train_model.py
Output: models/xgb_model.json  +  models/scaler.pkl
"""

import os
import json
import pickle
import warnings
import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, roc_auc_score, brier_score_loss
import xgboost as xgb

warnings.filterwarnings("ignore")

# ── Config ─────────────────────────────────────────────────────────────────────
load_dotenv()
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

# ── 1. Load raw data from MongoDB ──────────────────────────────────────────────
def load_matrix() -> pd.DataFrame:
    print("📦 Connecting to MongoDB...")
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client["easy_bets"]
    cursor = db["training_matrix"].find({}, {"_id": 0})
    df = pd.DataFrame(list(cursor))
    client.close()
    print(f"   Loaded {len(df):,} raw rows across {df['market_id'].nunique():,} markets.")
    return df


# ── 2. Feature Engineering ─────────────────────────────────────────────────────
# What % of a market's lifetime to use as the observation window.
# 0.5 = we only look at the first 50% of the market's life and predict the end.
# This simulates what you'd actually know about a live open market.
SNAPSHOT_PCT = 0.50

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapses each market's time-series into one feature row.

    IMPORTANT — leakage prevention:
      We only use the first SNAPSHOT_PCT of each market's lifetime.
      This simulates what we'd know about a live market mid-way through,
      and is what the live scanner will feed in for open markets.

    Features engineered (all from the early window only):
      Trajectory  – price at 10/25/50% of the observation window
      Momentum    – linear slope of price across the observation window
      Volatility  – std, max drawdown, direction flip count
      Conviction  – time spent above 0.7 / below 0.3 / near 0.5
      Liquidity   – number of observed data points (proxy for activity)
    """
    print(f"⚙️  Engineering features (observation window: first {int(SNAPSHOT_PCT*100)}% of lifetime)...")
    records = []

    for market_id, group in df.groupby("market_id"):
        g = group.sort_values("timestamp").reset_index(drop=True)
        all_prices = g["yes_price"].astype(float).values
        target = int(g["target"].iloc[0])
        n_total = len(all_prices)

        if n_total < 10:   # need enough points to have a meaningful window
            continue

        # ── Slice to observation window only ──────────────────────────────────
        cutoff = max(5, int(n_total * SNAPSHOT_PCT))
        prices = all_prices[:cutoff]
        n = len(prices)

        # ── Trajectory: price at quantiles of the observation window ──────────
        def price_at_pct(pct):
            idx = int(np.clip(pct * n, 0, n - 1))
            return float(prices[idx])

        p_open = price_at_pct(0.0)
        p25    = price_at_pct(0.25)
        p50    = price_at_pct(0.50)   # midpoint of our window (= 25% of full life)
        p75    = price_at_pct(0.75)
        p_now  = float(prices[-1])    # most recent price in the window

        # ── Momentum ──────────────────────────────────────────────────────────
        x = np.arange(n)
        slope = float(np.polyfit(x, prices, 1)[0])

        # slope of the last quarter of our window (recent acceleration)
        late_start = int(0.75 * n)
        slope_recent = float(
            np.polyfit(x[late_start:], prices[late_start:], 1)[0]
            if n - late_start > 2 else slope
        )

        # ── Volatility ────────────────────────────────────────────────────────
        diffs = np.diff(prices)
        volatility   = float(np.std(diffs))
        max_price    = float(np.max(prices))
        min_price    = float(np.min(prices))
        price_range  = max_price - min_price

        running_max  = np.maximum.accumulate(prices)
        drawdowns    = running_max - prices
        max_drawdown = float(np.max(drawdowns))

        signs  = np.sign(diffs)
        flips  = int(np.sum(np.diff(signs) != 0))

        # ── Conviction: time in strong-signal zones ───────────────────────────
        pct_above_70 = float(np.mean(prices > 0.70))
        pct_below_30 = float(np.mean(prices < 0.30))
        pct_near_50  = float(np.mean((prices > 0.45) & (prices < 0.55)))

        # ── Delta: how much has price moved so far ────────────────────────────
        delta_so_far  = p_now - p_open
        delta_recent  = p_now - p50

        # ── Liquidity proxy ───────────────────────────────────────────────────
        n_points = n

        records.append({
            # trajectory (window only — no future data)
            "p_open":        p_open,
            "p25":           p25,
            "p50":           p50,
            "p75":           p75,
            "p_now":         p_now,
            # momentum
            "slope":         slope,
            "slope_recent":  slope_recent,
            # volatility
            "volatility":    volatility,
            "max_price":     max_price,
            "min_price":     min_price,
            "price_range":   price_range,
            "max_drawdown":  max_drawdown,
            "flips":         flips,
            # conviction
            "pct_above_70":  pct_above_70,
            "pct_below_30":  pct_below_30,
            "pct_near_50":   pct_near_50,
            # deltas
            "delta_so_far":  delta_so_far,
            "delta_recent":  delta_recent,
            # liquidity
            "n_points":      n_points,
            # label
            "target":        target,
        })

    feat_df = pd.DataFrame(records)
    print(f"   Engineered {len(feat_df):,} market feature rows, "
          f"{feat_df['target'].mean()*100:.1f}% resolved YES.")
    return feat_df


# ── 3. Train XGBoost ───────────────────────────────────────────────────────────
FEATURE_COLS = [
    "p_open", "p25", "p50", "p75", "p_now",
    "slope", "slope_recent",
    "volatility", "max_price", "min_price", "price_range",
    "max_drawdown", "flips",
    "pct_above_70", "pct_below_30", "pct_near_50",
    "delta_so_far", "delta_recent",
    "n_points",
]

def train(feat_df: pd.DataFrame):
    X = feat_df[FEATURE_COLS].values
    y = feat_df["target"].values

    # Scale (helps XGBoost converge faster and makes features comparable)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=42, stratify=y
    )

    # Class imbalance: weight minority class
    pos_weight = float((y == 0).sum() / max((y == 1).sum(), 1))

    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=pos_weight,
        eval_metric="logloss",
        use_label_encoder=False,
        random_state=42,
        # EC2 free-tier friendly — limits RAM usage
        tree_method="hist",
        n_jobs=1,
    )

    print("\n🏋️  Training XGBoost...")
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # ── Evaluate ──────────────────────────────────────────────────────────────
    y_pred  = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print("\n── Test Set Metrics ──────────────────────────────────────────")
    print(classification_report(y_test, y_pred, target_names=["NO", "YES"]))
    print(f"ROC-AUC  : {roc_auc_score(y_test, y_proba):.4f}")
    print(f"Brier    : {brier_score_loss(y_test, y_proba):.4f}  (lower = better calibration)")

    # ── Cross-validation AUC ──────────────────────────────────────────────────
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(model, X_scaled, y, cv=cv, scoring="roc_auc")
    print(f"\n5-Fold CV AUC: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # ── Feature importances ───────────────────────────────────────────────────
    importances = pd.Series(
        model.feature_importances_, index=FEATURE_COLS
    ).sort_values(ascending=False)
    print("\n── Top 10 Feature Importances ────────────────────────────────")
    print(importances.head(10).to_string())

    return model, scaler


# ── 4. Save artefacts ──────────────────────────────────────────────────────────
def save_artifacts(model, scaler, feature_cols):
    model_path  = os.path.join(MODEL_DIR, "xgb_model.json")
    scaler_path = os.path.join(MODEL_DIR, "scaler.pkl")
    meta_path   = os.path.join(MODEL_DIR, "model_meta.json")

    model.save_model(model_path)
    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
    with open(meta_path, "w") as f:
        json.dump({"feature_cols": feature_cols}, f, indent=2)

    print(f"\n✅ Model saved  → {model_path}")
    print(f"✅ Scaler saved → {scaler_path}")
    print(f"✅ Meta saved   → {meta_path}")


# ── 5. Inference helper (used by the live scanner later) ──────────────────────
def load_model_and_score(feature_dict: dict) -> dict:
    """
    Given a dict of feature values for ONE market, returns:
      { predicted_class, probability_yes, edge }

    edge = how far the model's probability deviates from the current market price.
    A positive edge means the model thinks YES is underpriced.
    """
    model_path  = os.path.join(MODEL_DIR, "xgb_model.json")
    scaler_path = os.path.join(MODEL_DIR, "scaler.pkl")
    meta_path   = os.path.join(MODEL_DIR, "model_meta.json")

    with open(meta_path) as f:
        meta = json.load(f)

    cols = meta["feature_cols"]
    model = xgb.XGBClassifier()
    model.load_model(model_path)
    with open(scaler_path, "rb") as f:
        scaler = pickle.load(f)

    x = np.array([[feature_dict[c] for c in cols]])
    x_scaled = scaler.transform(x)
    prob_yes = float(model.predict_proba(x_scaled)[0][1])
    predicted = int(prob_yes >= 0.5)

    # edge vs current market price
    current_price = feature_dict.get("p_final", 0.5)
    edge = prob_yes - current_price

    return {
        "predicted_class": predicted,
        "probability_yes": round(prob_yes, 4),
        "current_price":   round(current_price, 4),
        "edge":            round(edge, 4),
    }


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    raw_df   = load_matrix()
    feat_df  = engineer_features(raw_df)

    if len(feat_df) < 50:
        print("⚠️  Too few markets to train reliably. Run build_mongo_matrix.py first.")
    else:
        model, scaler = train(feat_df)
        save_artifacts(model, scaler, FEATURE_COLS)
        print("\n🎯 Training complete. Run the live scanner next.")