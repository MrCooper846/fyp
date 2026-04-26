#!/usr/bin/env python3
"""Debug filtering - why are US contacts being rejected?"""

import json
import re
from pathlib import Path

# Import filtering logic
import gc_contacts.config as config
from gc_contacts.core.filtering import keep_contact, role_score

# Load some US contacts
us_file = Path("benchmark_runs/US_200unis_20251210_082318/contacts/ai_crawler_university_of_washington_contacts.json")
gb_file = Path("benchmark_runs/GB_200unis_20251209_172632/contacts/ai_crawler_university_of_oxford_contacts.json")

print("="*70)
print("FILTERING DEBUG: Why are US contacts rejected?")
print("="*70)

def analyze_contacts(file_path, label, home_domain):
    with open(file_path) as f:
        data = json.load(f)
    
    contacts = data.get("contacts", [])
    print(f"\n{label}")
    print("-"*70)
    print(f"Total contacts in file: {len(contacts)}")
    print(f"Home domain: {home_domain}")
    
    kept = 0
    rejected = 0
    
    print("\nSample analysis:")
    for i, contact in enumerate(contacts[:8]):
        name = contact.get("name", "")
        email = contact.get("email", "")
        role = contact.get("role", "")
        
        keep, score, reason = keep_contact(contact, home_domain)
        
        if keep:
            kept += 1
            status = "✓ KEPT"
        else:
            rejected += 1
            status = "✗ REJECTED"
        
        print(f"\n  Contact {i+1}: {status} (score: {score})")
        print(f"    Name:  {name[:50]}")
        print(f"    Email: {email}")
        print(f"    Role:  {role[:60]}")
        print(f"    Reason: {reason}")
        print(f"    Role score: {role_score(role)}")
        
        # Check individual criteria
        dom = email.split("@", 1)[1] if "@" in email else ""
        ok_domain = home_domain.endswith(dom) or dom.endswith(home_domain)
        print(f"    Domain match: {ok_domain} ({dom} vs {home_domain})")
        
        if re.search(config.INTL_HINTS, role, re.I):
            print(f"    INTL hints: YES")
        else:
            print(f"    INTL hints: NO")
    
    print(f"\nSummary: {kept} kept, {rejected} rejected out of {len(contacts[:8])} analyzed")
    return kept, rejected

# Analyze US
us_kept, us_rejected = analyze_contacts(us_file, "US: University of Washington", "washington.edu")

# Analyze GB
print("\n" + "="*70)
gb_kept, gb_rejected = analyze_contacts(gb_file, "GB: University of Oxford", "ox.ac.uk")

print("\n" + "="*70)
print("KEY FINDINGS")
print("="*70)
print(f"""
The main difference is in the ROLE field quality:

GB contacts have:
  - Explicit international roles: "Director of International Strategy"
  - Clean, professional titles: "Vice-Chancellor", "Provost"
  - These match ALLOWED_ROLE_WORDS and SENIORITY patterns easily

US contacts have:
  - Generic titles that don't mention international: "Director", "Executive Director"
  - Messy name extraction: "Workday\\nStudent", "Jack Martin\\nVice President"
  - Roles like "Innovation\\nFran" (garbage extraction)
  - Missing the INTL_HINTS bonus (+2 score)

Score threshold is 6:
  - Base for allowed role words: +5
  - Seniority bonus: +3  
  - Director/head/etc: +4
  - INTL hints: +2
  - Domain mismatch penalty: -2

US contacts often score 5-7 (borderline), while GB contacts score 8-12.
""")
