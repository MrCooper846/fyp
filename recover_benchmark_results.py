"""
Emergency recovery script for benchmark results that failed to save.
This will attempt to reload and re-save results using the fixed CSV export.
"""

import json
from pathlib import Path
from datetime import datetime

# Import after benchmark.py is fixed
from gc_contacts.benchmark import ComparisonReport, DiscoveryResult

def recover_run(run_dir: Path):
    """Attempt to recover a benchmark run that failed during CSV export."""
    
    # Check if results already exist
    results_csv = run_dir / "benchmark_results.csv"
    if results_csv.exists():
        print(f"✓ Results CSV already exists: {results_csv}")
        return
    
    # Check for metadata
    metadata_file = run_dir / "run_info.json"
    if not metadata_file.exists():
        print(f"✗ No metadata found in {run_dir}")
        return
    
    with open(metadata_file, 'r') as f:
        metadata = json.load(f)
    
    print(f"\nRecovering: {run_dir.name}")
    print(f"  Country: {metadata.get('country')}")
    print(f"  Universities: {metadata.get('total_universities')}")
    print(f"  Methods: {', '.join(metadata.get('methods', []))}")
    
    # Check for contact JSON files
    contacts_dir = run_dir / "contacts"
    if not contacts_dir.exists():
        print(f"✗ No contacts directory found")
        return
    
    contact_files = list(contacts_dir.glob("*.json"))
    print(f"  Found {len(contact_files)} contact files")
    
    # Reconstruct report from contact files
    report = ComparisonReport()
    
    for contact_file in contact_files:
        try:
            with open(contact_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Create DiscoveryResult from saved data
            result = DiscoveryResult(
                method=data.get("method", "unknown"),
                university_name=data.get("university", "unknown"),
                homepage_url=data.get("university_url", ""),
                candidates_found=data.get("summary", {}).get("candidates_found", 0),
                probe_attempts=data.get("summary", {}).get(
                    "probe_attempts",
                    data.get("summary", {}).get("candidates_probed", 0),
                ),
                candidates_probed=data.get("summary", {}).get("candidates_probed", 0),
                contacts_extracted=data.get("summary", {}).get("contacts_extracted", 0),
                contacts_kept=data.get("summary", {}).get("total_kept", 0),
                time_seconds=data.get("summary", {}).get("time_seconds", 0.0),
                tokens_in=0,  # Not saved in contact files
                tokens_out=0,
                cost_dollars=data.get("summary", {}).get("cost_dollars", 0.0),
                best_url=None,
                source_breakdown={},
                contacts_list=data.get("contacts", [])
            )
            report.add(result)
        except Exception as e:
            print(f"  ⚠ Failed to load {contact_file.name}: {e}")
    
    if not report.results:
        print(f"✗ No results could be recovered")
        return
    
    # Save CSV with fixed method
    print(f"  Saving CSV with {len(report.results)} results...")
    try:
        report.to_csv(results_csv)
        print(f"  ✓ Saved: {results_csv}")
    except Exception as e:
        print(f"  ✗ Failed to save CSV: {e}")


if __name__ == "__main__":
    benchmark_runs = Path("benchmark_runs")
    
    if not benchmark_runs.exists():
        print("No benchmark_runs directory found")
        exit(1)
    
    # Find all runs
    runs = sorted(benchmark_runs.glob("*_*unis_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    
    print(f"Found {len(runs)} benchmark runs")
    print("="*70)
    
    for run_dir in runs:
        recover_run(run_dir)
        print()
    
    print("="*70)
    print("Recovery complete!")
