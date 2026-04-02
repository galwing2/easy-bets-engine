"""
ml/base_rate_analysis.py
------------------------
Groups historical markets by question archetype and computes
actual resolution rates per category. Reveals systematic biases.

Run:  python -m ml.base_rate_analysis
      python -m ml.base_rate_analysis --min-markets 5
"""

import sys
import re
import os
import json
import argparse
from collections import defaultdict

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, ".")
from config import MONGO_URI, MONGO_DB

CATEGORIES = [
    ("election_win",         [r"win.*election", r"elected", r"win.*primary"]),
    ("election_candidate",   [r"nominee", r"nominate", r"candidate"]),
    ("political_approval",   [r"approval rating", r"favorability"]),
    ("political_action",     [r"sign.*bill", r"pass.*law", r"veto", r"impeach", r"resign"]),
    ("political_appoint",    [r"appoint", r"confirmed by senate"]),
    ("legal_verdict",        [r"convicted", r"acquitted", r"guilty", r"indicted", r"arrested"]),
    ("legal_ruling",         [r"supreme court", r"ruling", r"court.*decide", r"lawsuit"]),
    ("economic_rate",        [r"interest rate", r"fed.*rate", r"rate.*cut", r"rate.*hike"]),
    ("economic_threshold",   [r"gdp", r"inflation", r"unemployment", r"recession", r"cpi"]),
    ("crypto_price",         [r"bitcoin", r"ethereum", r"btc", r"eth", r"crypto"]),
    ("sports_championship",  [r"championship", r"super bowl", r"world series",
                               r"nba finals", r"stanley cup", r"world cup"]),
    ("sports_win",           [r"win.*game", r"beat ", r"defeat", r"playoffs"]),
    ("sports_award",         [r"mvp", r"heisman", r"cy young", r"award"]),
    ("sports_record",        [r"record", r"most.*season", r"break.*record"]),
    ("tech_product",         [r"release", r"launch", r"iphone", r"gpt-"]),
    ("tech_company",         [r"ipo", r"acquisition", r"merger", r"bankrupt", r"layoff"]),
    ("ai_model",             [r"gpt", r"claude", r"gemini", r"llm", r"ai model"]),
    ("geopolitical_conflict",[r"war", r"ceasefire", r"invasion", r"military", r"sanction"]),
    ("geopolitical_diplo",   [r"treaty", r"agreement", r"summit", r"diplomatic"]),
    ("deadline_by_date",     [r"by (?:january|february|march|april|may|june|"
                               r"july|august|september|october|november|december)",
                               r"before \d{4}", r"by end of", r"by q[1-4]"]),
    ("person_will",          [r"will (?:trump|biden|harris|musk|powell|"
                               r"zelensky|putin|xi|obama)"]),
    ("other",                [r".*"]),
]


def classify_question(question: str) -> str:
    q = question.lower().strip()
    for label, patterns in CATEGORIES:
        for pat in patterns:
            if re.search(pat, q):
                return label
    return "other"


def load_markets() -> pd.DataFrame:
    print("📦 Loading markets from MongoDB...")
    client = MongoClient(MONGO_URI)
    pipeline = [
        {"$group": {
            "_id":      "$market_id",
            "question": {"$first": "$question"},
            "target":   {"$first": "$target"},
            "n_points": {"$sum": 1},
        }}
    ]
    docs = list(client[MONGO_DB]["training_matrix"].aggregate(pipeline))
    client.close()

    df = pd.DataFrame(docs).rename(columns={"_id": "market_id"})
    print(f"   Loaded {len(df)} unique markets.")
    return df


def analyze(df: pd.DataFrame, min_markets: int = 3):
    df["category"] = df["question"].apply(classify_question)
    stats = []
    for cat, group in df.groupby("category"):
        n        = len(group)
        yes_rate = group["target"].mean()
        stats.append({
            "category":  cat,
            "n_markets": n,
            "yes_count": int(group["target"].sum()),
            "no_count":  int(n - group["target"].sum()),
            "yes_rate":  round(yes_rate, 4),
            "no_rate":   round(1 - yes_rate, 4),
            "bias_vs_50":round(yes_rate - 0.50, 4),
        })
    stats_df = (
        pd.DataFrame(stats)
        .query("n_markets >= @min_markets")
        .sort_values("n_markets", ascending=False)
        .reset_index(drop=True)
    )
    return stats_df, df


def print_report(stats_df: pd.DataFrame, all_df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("  BASE RATE ANALYSIS — YES Resolution Rates by Category")
    print("=" * 70)
    print(f"\n{'Category':<25} {'N':>5} {'YES%':>7} {'Bias':>8}  Signal")
    print("-" * 70)
    for _, row in stats_df.iterrows():
        bias   = row["bias_vs_50"]
        signal = (
            "  ⬆️  underpriced YES" if bias > 0.15 else
            "  ↑  slight YES lean"  if bias > 0.05 else
            "  ⬇️  overpriced YES"  if bias < -0.15 else
            "  ↓  slight NO lean"   if bias < -0.05 else
            "  neutral"
        )
        print(f"{row['category']:<25} {row['n_markets']:>5} "
              f"{row['yes_rate']*100:>6.1f}%  {bias:>+.3f}  {signal}")

    # Save JSON for live scanner
    base_rates = {
        row["category"]: {"yes_rate": row["yes_rate"], "n_markets": int(row["n_markets"])}
        for _, row in stats_df.iterrows()
    }
    os.makedirs("model", exist_ok=True)
    out = "model/base_rates.json"
    with open(out, "w") as f:
        json.dump(base_rates, f, indent=2)
    print(f"\n✅ Base rates saved → {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-markets", type=int, default=3)
    args = parser.parse_args()

    df = load_markets()
    if len(df) < 10:
        print("⚠️  Too few markets. Run ingestion/build_mongo_matrix.py first.")
    else:
        stats_df, all_df = analyze(df, min_markets=args.min_markets)
        print_report(stats_df, all_df)
