"""
base_rate_analysis.py
---------------------
Groups historical markets by question archetype and computes
actual resolution rates per category. Reveals systematic biases
where the crowd consistently over- or under-prices certain question types.

Run:  python base_rate_analysis.py
      python base_rate_analysis.py --min-markets 5   # stricter minimum
"""

import os
import re
import json
import argparse
from collections import defaultdict
import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# ── Category definitions ───────────────────────────────────────────────────────
# Each category is a (label, [keyword patterns]) tuple.
# A market is assigned the FIRST matching category.
# Order matters — put more specific patterns first.

CATEGORIES = [
    # ── Political / Elections ──────────────────────────────────────────────────
    ("election_win",        [r"win.*election", r"elected", r"win.*primary",
                             r"win.*vote", r"win.*seat", r"win.*race"]),
    ("election_candidate",  [r"nominee", r"nominate", r"candidate",
                             r"run for", r"ballot"]),
    ("political_approval",  [r"approval rating", r"favorability",
                             r"poll.*above", r"poll.*below"]),
    ("political_action",    [r"sign.*bill", r"pass.*law", r"veto",
                             r"impeach", r"resign", r"removed from"]),
    ("political_appoint",   [r"appoint", r"nominate.*(?:secretary|judge|chair|director)",
                             r"confirmed by senate"]),

    # ── Legal / Judicial ──────────────────────────────────────────────────────
    ("legal_verdict",       [r"convicted", r"acquitted", r"guilty",
                             r"indicted", r"charged", r"arrested",
                             r"verdict", r"plead"]),
    ("legal_ruling",        [r"supreme court", r"ruling", r"court.*decide",
                             r"overturned", r"upheld", r"lawsuit"]),

    # ── Economics / Markets ───────────────────────────────────────────────────
    ("economic_rate",       [r"interest rate", r"fed.*rate", r"rate.*cut",
                             r"rate.*hike", r"basis point"]),
    ("economic_threshold",  [r"gdp", r"inflation", r"unemployment",
                             r"recession", r"cpi", r"above \d", r"below \d",
                             r"reach \$", r"hit \$", r"exceed"]),
    ("crypto_price",        [r"bitcoin", r"ethereum", r"btc", r"eth",
                             r"crypto", r"coin.*\$", r"\$.*btc"]),

    # ── Sports ────────────────────────────────────────────────────────────────
    ("sports_championship", [r"championship", r"super bowl", r"world series",
                             r"nba finals", r"stanley cup", r"world cup",
                             r"champions league"]),
    ("sports_win",          [r"win.*game", r"beat ", r"defeat",
                             r"playoffs", r"advance to"]),
    ("sports_award",        [r"mvp", r"heisman", r"cy young", r"golden glove",
                             r"ballon d.or", r"award"]),
    ("sports_record",       [r"record", r"most.*season", r"first.*to",
                             r"break.*record"]),

    # ── Tech / Business ───────────────────────────────────────────────────────
    ("tech_product",        [r"release", r"launch", r"announce.*(?:product|model|version|update)",
                             r"iphone", r"gpt-", r"model \d"]),
    ("tech_company",        [r"ipo", r"acquisition", r"merger", r"acquired by",
                             r"bankrupt", r"layoff", r"valuation"]),
    ("ai_model",            [r"gpt", r"claude", r"gemini", r"llm",
                             r"ai model", r"artificial intelligence.*(?:beat|surpass|achieve)"]),

    # ── Geopolitical ──────────────────────────────────────────────────────────
    ("geopolitical_conflict",[r"war", r"ceasefire", r"invasion", r"attack",
                              r"military", r"troops", r"sanction"]),
    ("geopolitical_diplo",  [r"treaty", r"agreement", r"summit", r"negotiate",
                             r"diplomatic"]),

    # ── Deadline / Date-based ─────────────────────────────────────────────────
    ("deadline_by_date",    [r"by (?:january|february|march|april|may|june|"
                              r"july|august|september|october|november|december)",
                             r"before \d{4}", r"by end of",
                             r"by q[1-4]", r"this year", r"in \d{4}"]),

    # ── Person-specific ───────────────────────────────────────────────────────
    ("person_will",         [r"will (?:trump|biden|harris|musk|powell|"
                              r"zelensky|putin|xi|obama|clinton)"]),

    # ── Catch-all ────────────────────────────────────────────────────────────
    ("other",               [r".*"]),
]


def classify_question(question: str) -> str:
    q = question.lower().strip()
    for label, patterns in CATEGORIES:
        for pat in patterns:
            if re.search(pat, q):
                return label
    return "other"


# ── Load data ──────────────────────────────────────────────────────────────────
def load_markets() -> pd.DataFrame:
    print("📦 Loading markets from MongoDB...")
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client["easy_bets"]

    # One row per market — get the question and target
    pipeline = [
        {"$group": {
            "_id": "$market_id",
            "question": {"$first": "$question"},
            "target":   {"$first": "$target"},
            "n_points": {"$sum": 1},
        }}
    ]
    docs = list(db["training_matrix"].aggregate(pipeline))
    client.close()

    df = pd.DataFrame(docs)
    df.rename(columns={"_id": "market_id"}, inplace=True)
    print(f"   Loaded {len(df)} unique markets.")
    return df


# ── Analyze ────────────────────────────────────────────────────────────────────
def analyze(df: pd.DataFrame, min_markets: int = 3) -> pd.DataFrame:
    df["category"] = df["question"].apply(classify_question)

    stats = []
    for cat, group in df.groupby("category"):
        n        = len(group)
        yes_rate = group["target"].mean()
        yes_n    = group["target"].sum()
        no_n     = n - yes_n

        # Crowd implied price — average opening price (p_open would be better,
        # but we store yes_price time series; use 0.5 as neutral baseline for now.
        # The real bias check happens vs. historical base rates below.)

        stats.append({
            "category":    cat,
            "n_markets":   n,
            "yes_count":   int(yes_n),
            "no_count":    int(no_n),
            "yes_rate":    round(yes_rate, 4),
            "no_rate":     round(1 - yes_rate, 4),
            # bias: how far from 50/50
            "bias_vs_50":  round(yes_rate - 0.50, 4),
        })

    stats_df = pd.DataFrame(stats)
    stats_df = stats_df[stats_df["n_markets"] >= min_markets]
    stats_df = stats_df.sort_values("n_markets", ascending=False).reset_index(drop=True)
    return stats_df, df


def print_report(stats_df: pd.DataFrame, all_df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("  BASE RATE ANALYSIS — Historical YES Resolution Rates by Category")
    print("=" * 70)
    print(f"\n{'Category':<25} {'N':>5} {'YES%':>7} {'Bias vs 50%':>12}  Signal")
    print("-" * 70)

    for _, row in stats_df.iterrows():
        bias = row["bias_vs_50"]
        if abs(bias) < 0.05:
            signal = "  neutral"
        elif bias > 0.15:
            signal = "  ⬆️  crowd may UNDERprice YES"
        elif bias > 0.05:
            signal = "  ↑  slight YES lean"
        elif bias < -0.15:
            signal = "  ⬇️  crowd may OVERprice YES (fade it)"
        else:
            signal = "  ↓  slight NO lean"

        print(
            f"{row['category']:<25} {row['n_markets']:>5} "
            f"{row['yes_rate']*100:>6.1f}%  "
            f"{bias:>+.3f}       {signal}"
        )

    print("\n" + "=" * 70)
    print("  MOST INTERESTING CATEGORIES (biggest bias, enough sample size)")
    print("=" * 70)

    # Show categories with strong bias and at least 5 markets
    interesting = stats_df[
        (stats_df["n_markets"] >= 5) &
        (stats_df["bias_vs_50"].abs() >= 0.10)
    ].sort_values("bias_vs_50", key=abs, ascending=False)

    if interesting.empty:
        print("\n  Not enough data yet — run with a larger --limit to see biases.")
    else:
        for _, row in interesting.iterrows():
            direction = "YES is UNDERPRICED" if row["bias_vs_50"] > 0 else "YES is OVERPRICED (bet NO)"
            print(f"\n  [{row['category']}]")
            print(f"    Actual YES rate : {row['yes_rate']*100:.1f}%")
            print(f"    Markets in set  : {row['n_markets']}")
            print(f"    Takeaway        : {direction} in this category")

            # Show sample questions from this category
            samples = all_df[all_df["category"] == row["category"]]["question"].head(3).tolist()
            print("    Examples:")
            for q in samples:
                print(f"      – {q[:80]}")

    print("\n" + "=" * 70)
    print("  OVERALL DATASET STATS")
    print("=" * 70)
    print(f"  Total markets       : {len(all_df)}")
    print(f"  Overall YES rate    : {all_df['target'].mean()*100:.1f}%")
    print(f"  Categories found    : {all_df['category'].nunique()}")
    print(f"  Uncategorized (other): {(all_df['category'] == 'other').sum()}")

    # Save results to JSON for the live scanner to use
    base_rates = {}
    for _, row in stats_df.iterrows():
        base_rates[row["category"]] = {
            "yes_rate":  row["yes_rate"],
            "n_markets": int(row["n_markets"]),
        }

    out_path = "models/base_rates.json"
    os.makedirs("models", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(base_rates, f, indent=2)
    print(f"\n✅ Base rates saved → {out_path}  (live scanner will use this)")


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-markets", type=int, default=3,
                        help="Minimum markets per category to include in report")
    args = parser.parse_args()

    df = load_markets()

    if len(df) < 10:
        print("⚠️  Too few markets. Run build_mongo_matrix.py --limit 4000 first.")
    else:
        stats_df, all_df = analyze(df, min_markets=args.min_markets)
        print_report(stats_df, all_df)