#!/usr/bin/env python3
"""Systematic analysis of US vs GB performance issues."""

import pandas as pd
import json
from pathlib import Path

# Load CSVs
gb = pd.read_csv('benchmark_runs/GB_200unis_20251209_172632/benchmark_results.csv')
us = pd.read_csv('benchmark_runs/US_200unis_20251210_082318/benchmark_results.csv')

print("="*80)
print("ROOT CAUSE ANALYSIS: US vs GB Performance")
print("="*80)

# Problem 1: Discovery failure
print("\n1. DISCOVERY PHASE - Finding candidate URLs")
print("-"*80)

gb_zero_cands = gb[gb['candidates_found'] == 0]
us_zero_cands = us[us['candidates_found'] == 0]

print(f"\nUniversities with ZERO candidates found (complete discovery failure):")
print(f"  GB: {len(gb_zero_cands)}/600 attempts ({len(gb_zero_cands)/6:.1f}%)")
print(f"  US: {len(us_zero_cands)}/600 attempts ({len(us_zero_cands)/6:.1f}%)")

# Sample of failures
print(f"\nTop US universities where ALL methods found 0 candidates:")
us_complete_failures = us[us['candidates_found'] == 0].groupby('university').size()
us_complete_failures = us_complete_failures[us_complete_failures == 3]  # All 3 methods failed
for uni in us_complete_failures.head(10).index:
    print(f"  - {uni}")

# Problem 2: Extraction quality
print("\n\n2. EXTRACTION PHASE - Getting contacts from pages")
print("-"*80)

gb_with_cands = gb[gb['candidates_found'] > 0]
us_with_cands = us[us['candidates_found'] > 0]

gb_extraction_rate = gb_with_cands['contacts_extracted'].sum() / gb_with_cands['candidates_probed'].sum()
us_extraction_rate = us_with_cands['contacts_extracted'].sum() / us_with_cands['candidates_probed'].sum()

print(f"\nContacts extracted per probed URL (when candidates exist):")
print(f"  GB: {gb_extraction_rate:.2f} contacts per URL")
print(f"  US: {us_extraction_rate:.2f} contacts per URL")
print(f"  → US extraction is {gb_extraction_rate/us_extraction_rate:.1f}x worse")

# Problem 3: Filtering
print("\n\n3. FILTERING PHASE - Relevance filtering")
print("-"*80)

gb_filter_rate = gb['contacts_kept'].sum() / gb['contacts_extracted'].sum() if gb['contacts_extracted'].sum() > 0 else 0
us_filter_rate = us['contacts_kept'].sum() / us['contacts_extracted'].sum() if us['contacts_extracted'].sum() > 0 else 0

print(f"\nFiltering pass rate (kept / extracted):")
print(f"  GB: {gb_filter_rate*100:.1f}% of extracted contacts are kept")
print(f"  US: {us_filter_rate*100:.1f}% of extracted contacts are kept")
print(f"  → US contacts are {gb_filter_rate/us_filter_rate:.1f}x more likely to be filtered out")

# Root causes
print("\n\n4. ROOT CAUSES")
print("="*80)

print("""
DISCOVERY FAILURE (69.5% of US universities vs 27% GB):
  ❌ US universities use different URL structures
  ❌ Heuristic patterns optimized for UK domains (.ac.uk structure)
  ❌ US universities (.edu) have more varied navigation structures
  → FIX: Add US-specific URL patterns to SLUGS in config.py
  → FIX: Train AI methods on US university examples

EXTRACTION QUALITY (0.83 contacts/URL vs 1.47 for GB):
  ❌ US pages may have different HTML structures
  ❌ GPT extraction might be tuned for UK-style pages
  ⚠️  Need to inspect actual US pages to diagnose
  → FIX: Review GPT prompts for US compatibility
  → FIX: Add US university examples to training data

FILTERING STRICTNESS (3.5% pass rate vs 11.9% for GB):
  ❌ US contacts lack "international" in role titles
  ❌ US organizations use "Global", "Worldwide", "International Relations"
  ❌ Current ALLOWED_ROLE_WORDS may miss US terminology
  → FIX: Expand ALLOWED_ROLE_WORDS to include US variants
  → FIX: Lower score threshold or add US-specific patterns
  → FIX: Check if "global" is properly matched (it should be!)

IMMEDIATE ACTION:
  1. Check if "global" is in ALLOWED_ROLE_WORDS (it should be but verify)
  2. Add US-specific URL patterns
  3. Review a few US university pages manually
  4. Consider lowering score threshold from 6 to 5
""")

# Check specific role matching
print("\n5. ROLE MATCHING TEST")
print("-"*80)
import re
import gc_contacts.config as config

test_roles = [
    "Director of International Recruitment",  # GB style
    "Director of Global Admissions",  # US style
    "Vice President for Global Engagement",  # US style
    "Head of Worldwide Partnerships",  # Alternative
    "Director",  # Generic
]

for role in test_roles:
    match = bool(re.search(config.ALLOWED_ROLE_WORDS, role, re.I))
    print(f"  '{role}': {'✓ MATCHES' if match else '✗ NO MATCH'}")
