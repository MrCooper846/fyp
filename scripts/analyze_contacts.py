#!/usr/bin/env python3
"""
Analyze contact data exported from benchmark runs.

Usage:
    python analyze_contacts.py --dir benchmark_contacts
"""

import argparse
import json
from pathlib import Path
from collections import defaultdict


def analyze_contacts():
    parser = argparse.ArgumentParser(
        description="Analyze contact data from benchmark runs"
    )
    parser.add_argument(
        "--dir",
        default="benchmark_contacts",
        help="Directory containing contact JSON files"
    )
    parser.add_argument(
        "--method",
        help="Filter by method (heuristic, ai_slug, ai_crawler)"
    )
    
    args = parser.parse_args()
    contacts_dir = Path(args.contacts_dir)
    
    if not contacts_dir.exists():
        print(f"Directory not found: {contacts_dir}")
        return
    
    # Load and aggregate contact files
    all_files = sorted(contacts_dir.glob("*.json"))
    by_method = defaultdict(lambda: {"unis": [], "total_contacts": 0, "sample_contacts": []})
    
    for file_path in all_files:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        method = data["method"]
        if args.method and method != args.method:
            continue
        
        uni_name = data["university"]
        contacts = data["contacts"]
        
        by_method[method]["unis"].append(uni_name)
        by_method[method]["total_contacts"] += len(contacts)
        by_method[method]["sample_contacts"].extend(contacts[:3])  # Keep first 3 samples
    
    # Print report
    print("\n" + "="*120)
    print("CONTACT DATA ANALYSIS")
    print("="*120)
    
    for method in ["heuristic", "ai_slug", "ai_crawler"]:
        if method not in by_method:
            print(f"\n{method.upper()}: NOT RUN")
            continue
        
        stats = by_method[method]
        print(f"\n{method.upper()}:")
        print(f"  Universities processed: {len(stats['unis'])}")
        print(f"  Total contacts found:   {stats['total_contacts']}")
        print(f"  Avg per university:     {stats['total_contacts'] / len(stats['unis']):.1f}")
        
        if stats["sample_contacts"]:
            print(f"  Sample contacts:")
            for c in stats["sample_contacts"][:5]:
                name = c.get("name", "N/A")
                email = c.get("email", "N/A")
                role = c.get("role", "N/A")
                source = c.get("page_url", "N/A")
                print(f"    • {name} <{email}> ({role}) from {source[:60]}")
    
    print("\n" + "="*120)
    
    # Show detailed contact list
    if args.method:
        print(f"\nAll contacts for {args.method.upper()}:")
        for method in [args.method]:
            if method in by_method:
                stats = by_method[method]
                for i, c in enumerate(stats["sample_contacts"][:20], 1):
                    name = c.get("name", "N/A")
                    email = c.get("email", "N/A")
                    role = c.get("role", "N/A")
                    source = c.get("page_url", "N/A")
                    print(f"{i:2d}. {name:<30} {email:<40} {role:<30}")
                    print(f"    Source: {source}\n")


if __name__ == "__main__":
    analyze_contacts()
