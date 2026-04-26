#!/usr/bin/env python3
"""
Helper script to list and compare benchmark runs.

Usage:
    python list_runs.py              # Show all runs
    python list_runs.py --details    # Show details for each run
"""

import argparse
import json
from pathlib import Path
from datetime import datetime


def format_size(size_bytes):
    """Format bytes to human readable."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def get_folder_size(path):
    """Get total size of folder."""
    total = 0
    for p in path.rglob('*'):
        if p.is_file():
            total += p.stat().st_size
    return total


def list_runs(base_dir="benchmark_runs", show_details=False):
    """List all benchmark runs."""
    base_path = Path(base_dir)
    
    if not base_path.exists():
        print(f"No runs found. Directory {base_dir} does not exist.")
        return
    
    runs = sorted(base_path.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not runs:
        print(f"No runs found in {base_dir}")
        return
    
    print("\n" + "="*100)
    print("BENCHMARK RUNS")
    print("="*100 + "\n")
    
    for i, run_dir in enumerate(runs, 1):
        if not run_dir.is_dir():
            continue
        
        # Read metadata
        metadata_file = run_dir / "run_info.json"
        if metadata_file.exists():
            with open(metadata_file) as f:
                metadata = json.load(f)
            
            timestamp = metadata.get("timestamp", "")
            country = metadata.get("country", "N/A")
            num_unis = metadata.get("num_universities", "?")
            methods = ", ".join(metadata.get("methods", []))
            
            # Get folder size
            folder_size = get_folder_size(run_dir)
            
            # Count contacts
            contacts_dir = run_dir / "contacts"
            contact_files = list(contacts_dir.glob("*.json")) if contacts_dir.exists() else []
            
            print(f"{i}. {run_dir.name}")
            print(f"   Time:       {timestamp}")
            print(f"   Country:    {country}")
            print(f"   Unis:       {num_unis}")
            print(f"   Methods:    {methods}")
            print(f"   Size:       {format_size(folder_size)}")
            print(f"   Contacts:   {len(contact_files)} files")
            
            if show_details:
                # Show stats
                results_file = run_dir / "benchmark_results.csv"
                if results_file.exists():
                    with open(results_file) as f:
                        lines = f.readlines()
                    print(f"   Stats:      {len(lines) - 1} result rows")
                    
                    # Show brief stats
                    total_contacts = 0
                    total_cost = 0.0
                    for line in lines[1:]:
                        parts = line.strip().split(',')
                        if len(parts) > 5:
                            try:
                                total_contacts += int(parts[5])
                                total_cost += float(parts[9])
                            except (ValueError, IndexError):
                                pass
                    
                    print(f"   Total:      {total_contacts} contacts, ${total_cost:.2f}")
            
            print()
    
    print("="*100)
    print(f"Total runs: {len(runs)}")
    print("="*100 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List benchmark runs")
    parser.add_argument("--details", action="store_true", help="Show detailed information")
    parser.add_argument("--dir", default="benchmark_runs", help="Benchmark directory")
    args = parser.parse_args()
    
    list_runs(args.dir, args.details)
