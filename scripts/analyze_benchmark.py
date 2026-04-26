#!/usr/bin/env python3
"""
Analyze benchmark results and generate insights.

Usage:
    python analyze_benchmark.py benchmark_results.csv
"""

import argparse
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List


def analyze_results(csv_path: Path) -> None:
    """Analyze benchmark results."""
    df = pd.read_csv(csv_path)
    
    print("\n" + "="*100)
    print("DISCOVERY METHOD ANALYSIS")
    print("="*100)
    
    # By method statistics
    print("\n1. CONTACTS EFFICIENCY")
    print("-" * 100)
    by_method = df.groupby("method").agg({
        "contacts_kept": ["sum", "mean", "median"],
        "time_seconds": ["mean", "median"],
        "cost_dollars": ["sum", "mean"],
        "university": "count"
    }).round(2)
    by_method.columns = ["_".join(col).strip() for col in by_method.columns]
    print(by_method)
    
    # Success rate
    print("\n2. SUCCESS RATE (% of unis with >= 1 contact)")
    print("-" * 100)
    success = df.groupby("method").apply(
        lambda x: (x["contacts_kept"] >= 1).sum() / len(x) * 100
    ).round(1)
    for method, rate in success.items():
        print(f"  {method:15s}: {rate:5.1f}%")
    
    # Cost efficiency
    print("\n3. COST ANALYSIS")
    print("-" * 100)
    for method in df["method"].unique():
        subset = df[df["method"] == method]
        total_cost = subset["cost_dollars"].sum()
        total_contacts = subset["contacts_kept"].sum()
        cost_per_contact = total_cost / total_contacts if total_contacts > 0 else float('inf')
        print(f"  {method:15s}:")
        print(f"    Total cost:         ${total_cost:.2f}")
        print(f"    Total contacts:     {total_contacts}")
        print(f"    Cost per contact:   ${cost_per_contact:.4f}")
    
    # Time efficiency
    print("\n4. TIME ANALYSIS")
    print("-" * 100)
    for method in df["method"].unique():
        subset = df[df["method"] == method]
        total_time = subset["time_seconds"].sum()
        total_contacts = subset["contacts_kept"].sum()
        time_per_contact = total_time / total_contacts if total_contacts > 0 else float('inf')
        print(f"  {method:15s}:")
        print(f"    Total time:         {total_time:.1f}s")
        print(f"    Total contacts:     {total_contacts}")
        print(f"    Time per contact:   {time_per_contact:.2f}s")
    
    # Quality metrics
    print("\n5. EXTRACTION QUALITY")
    print("-" * 100)
    for method in df["method"].unique():
        subset = df[df["method"] == method]
        precision = (subset["contacts_kept"] / subset["contacts_extracted"]).mean() * 100
        extraction_rate = subset["contacts_extracted"].mean()
        print(f"  {method:15s}:")
        print(f"    Avg extracted/uni:  {extraction_rate:.1f}")
        print(f"    Avg kept/uni:       {subset['contacts_kept'].mean():.1f}")
        print(f"    Avg precision:      {precision:.1f}%")
    
    # Ranking
    print("\n6. RECOMMENDATIONS")
    print("-" * 100)
    
    # Score each method
    scores = {}
    for method in df["method"].unique():
        subset = df[df["method"] == method]
        
        # Metrics (normalized 0-1)
        contacts_score = subset["contacts_kept"].sum() / df["contacts_kept"].sum() if df["contacts_kept"].sum() > 0 else 0
        success_score = (subset["contacts_kept"] >= 1).sum() / len(subset)
        cost_score = 1 - (subset["cost_dollars"].sum() / df["cost_dollars"].sum()) if df["cost_dollars"].sum() > 0 else 1
        time_score = 1 - (subset["time_seconds"].sum() / df["time_seconds"].sum()) if df["time_seconds"].sum() > 0 else 1
        
        # Weighted score
        overall_score = (
            contacts_score * 0.4 +  # 40% on total contacts
            success_score * 0.2 +   # 20% on success rate
            cost_score * 0.2 +      # 20% on cost efficiency
            time_score * 0.2        # 20% on speed
        )
        scores[method] = {
            "overall": overall_score,
            "contacts": contacts_score,
            "success": success_score,
            "cost": cost_score,
            "time": time_score,
        }
    
    # Print rankings
    ranked = sorted(scores.items(), key=lambda x: x[1]["overall"], reverse=True)
    for rank, (method, scores_dict) in enumerate(ranked, 1):
        print(f"\n  {rank}. {method.upper()}")
        print(f"     Overall score:     {scores_dict['overall']:.2f}/1.00")
        print(f"     Contacts score:    {scores_dict['contacts']:.2f}")
        print(f"     Success score:     {scores_dict['success']:.2f}")
        print(f"     Cost score:        {scores_dict['cost']:.2f}")
        print(f"     Time score:        {scores_dict['time']:.2f}")
    
    # Per-university comparison
    print("\n7. PER-UNIVERSITY BREAKDOWN")
    print("-" * 100)
    pivot = df.pivot_table(
        values="contacts_kept",
        index="university",
        columns="method",
        aggfunc="first"
    )
    print(pivot.to_string())
    
    print("\n" + "="*100)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze benchmark results")
    parser.add_argument("results_csv", help="Benchmark results CSV file")
    args = parser.parse_args()
    
    csv_path = Path(args.results_csv)
    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        exit(1)
    
    analyze_results(csv_path)
