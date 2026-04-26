#!/usr/bin/env python3
"""Check actual filtering results for US universities."""

import json
from pathlib import Path
from gc_contacts.core.filtering import keep_contact

us_harvard = Path("benchmark_runs/US_200unis_20251210_082318/contacts/ai_crawler_harvard_university_contacts.json")

with open(us_harvard) as f:
    data = json.load(f)

contacts = data.get("contacts", [])
home_domain = "harvard.edu"

print(f"Harvard University Analysis")
print(f"Total extracted: {len(contacts)}")
print(f"Summary says kept: {data['summary']['total_kept']}")
print()

kept_count = 0
rejected_count = 0

print("Checking each contact:")
for i, contact in enumerate(contacts[:10], 1):
    keep, score, reason = keep_contact(contact, home_domain)
    status = "✓ KEPT" if keep else "✗ REJECTED"
    
    if keep:
        kept_count += 1
    else:
        rejected_count += 1
    
    print(f"\n{i}. {status} (score: {score}, reason: {reason})")
    print(f"   Name:  {contact.get('name', '')[:60]}")
    print(f"   Email: {contact.get('email', '')}")
    print(f"   Role:  {contact.get('role', '')[:60]}")

print(f"\n\nActual filtering: {kept_count} kept, {rejected_count} rejected")
print(f"Expected from summary: {data['summary']['total_kept']} kept")
