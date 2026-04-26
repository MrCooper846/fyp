#!/usr/bin/env python3
"""Quick analysis script to compare GB vs US benchmark results."""

import pandas as pd

# Load data
gb = pd.read_csv('benchmark_runs/GB_200unis_20251209_172632/benchmark_results.csv')
us = pd.read_csv('benchmark_runs/US_200unis_20251210_082318/benchmark_results.csv')

print("="*70)
print("BENCHMARK COMPARISON: GB vs US")
print("="*70)

print("\n1. OVERALL PERFORMANCE")
print("-" * 70)
print("\nGB - Average contacts per university:")
print(gb.groupby('method')['contacts_kept'].mean().round(2))
print("\nUS - Average contacts per university:")
print(us.groupby('method')['contacts_kept'].mean().round(2))

print("\n\n2. DISCOVERY SUCCESS RATE (found candidates)")
print("-" * 70)
gb_found = gb[gb['candidates_found'] > 0].groupby('method').size()
us_found = us[us['candidates_found'] > 0].groupby('method').size()

print(f"\nGB - Universities with candidates found:")
for method in ['heuristic', 'ai_slug', 'ai_crawler']:
    count = gb_found.get(method, 0)
    print(f"  {method:12s}: {count}/200 ({count/2:.1f}%)")

print(f"\nUS - Universities with candidates found:")
for method in ['heuristic', 'ai_slug', 'ai_crawler']:
    count = us_found.get(method, 0)
    print(f"  {method:12s}: {count}/200 ({count/2:.1f}%)")

print("\n\n3. FILTERING RATE (kept vs extracted)")
print("-" * 70)
print("\nGB - Percentage of extracted contacts that pass filtering:")
for method in ['heuristic', 'ai_slug', 'ai_crawler']:
    extracted = gb[gb['method'] == method]['contacts_extracted'].sum()
    kept = gb[gb['method'] == method]['contacts_kept'].sum()
    rate = (kept / extracted * 100) if extracted > 0 else 0
    print(f"  {method:12s}: {rate:.1f}% ({kept}/{extracted})")

print("\nUS - Percentage of extracted contacts that pass filtering:")
for method in ['heuristic', 'ai_slug', 'ai_crawler']:
    extracted = us[us['method'] == method]['contacts_extracted'].sum()
    kept = us[us['method'] == method]['contacts_kept'].sum()
    rate = (kept / extracted * 100) if extracted > 0 else 0
    print(f"  {method:12s}: {rate:.1f}% ({kept}/{extracted})")

print("\n\n4. SAMPLE FAILURES (US universities with 0 results)")
print("-" * 70)
us_failures = us[(us['method'] == 'ai_crawler') & (us['candidates_found'] == 0)]['university'].head(10)
print("\nUniversities where AI Crawler found ZERO candidates:")
for i, uni in enumerate(us_failures, 1):
    print(f"  {i}. {uni}")

print("\n\n5. KEY INSIGHTS")
print("-" * 70)
gb_zero_heuristic = (gb[gb['method'] == 'heuristic']['candidates_found'] == 0).sum()
us_zero_heuristic = (us[us['method'] == 'heuristic']['candidates_found'] == 0).sum()

print(f"""
Discovery Phase:
  - GB: Heuristic found 0 candidates on {gb_zero_heuristic}/200 universities ({gb_zero_heuristic/2:.1f}%)
  - US: Heuristic found 0 candidates on {us_zero_heuristic}/200 universities ({us_zero_heuristic/2:.1f}%)
  → US websites may use different URL patterns

Filtering Phase:
  - GB: {gb['contacts_kept'].sum()/gb['contacts_extracted'].sum()*100:.1f}% of extracted contacts pass filtering
  - US: {us['contacts_kept'].sum()/us['contacts_extracted'].sum()*100:.1f}% of extracted contacts pass filtering
  → US contacts are being filtered out at a much higher rate

Overall Performance:
  - GB: {gb['contacts_kept'].mean():.2f} relevant contacts per university on average
  - US: {us['contacts_kept'].mean():.2f} relevant contacts per university on average
  → US is performing {gb['contacts_kept'].mean() / us['contacts_kept'].mean():.1f}x worse than GB
""")

print("="*70)
