# US Benchmark Performance Issues & Recommendations

**Date:** December 10, 2025  
**Analysis:** Comparison of GB_200unis_20251209_172632 vs US_200unis_20251210_082318

## Executive Summary

US benchmark results are **8.9x worse** than GB results (0.25 vs 2.25 relevant contacts per university). The system is heavily optimized for UK universities and fails on US institutions.

## Performance Comparison

| Metric | GB (200 unis) | US (200 unis) | Ratio |
|--------|---------------|---------------|-------|
| Avg contacts kept per uni | 2.25 | 0.25 | **8.9x worse** |
| Discovery failure rate | 27.0% | 65.7% | **2.4x worse** |
| Filtering pass rate | 11.9% | 3.5% | **3.4x stricter** |
| Contacts extracted per URL | 2.59 | 2.14 | 1.2x worse |

## Root Causes (Priority Order)

### 1. 🔴 CRITICAL: Discovery Failure (66% failure rate)

**Problem:**
- 394/600 attempts (65.7%) found ZERO candidates in US
- Only 162/600 (27%) in GB
- Major universities completely failed: Harvard, Stanford, MIT, Berkeley, Yale, Columbia, Cornell, UPenn

**Root Cause:**
- Heuristic URL patterns in `config.py` optimized for UK `.ac.uk` structure
- US `.edu` universities have completely different navigation and URL patterns
- SLUGS list has UK-centric patterns like `/international/`, `/admissions/`
- US universities often use `/about/leadership/`, `/administration/officers/`, `/president/`, `/provost/`

**Impact:** 
This is the PRIMARY bottleneck. Even if extraction/filtering were perfect, 66% of attempts find nothing.

### 2. 🟡 MAJOR: Filtering Too Strict (3.4x higher rejection)

**Problem:**
- Only 3.5% of US extracted contacts pass filtering vs 11.9% for GB
- Score threshold of 6 is borderline for many US contacts
- US contacts score 5-7 range, GB contacts score 8-12 range

**Root Cause:**
- Domain mismatch penalty (-2) hurts US more (e.g., `uw.edu` vs `washington.edu`)
- US roles less likely to explicitly mention "international" (use "global", "worldwide" instead)
- Generic titles like "Director" without context get lower scores
- Role matching regex DOES work (tested: "global", "admissions", "engagement" all match)

**Impact:**
Even when contacts are found, most are thrown away.

### 3. 🟢 MINOR: Extraction Quality (1.2x worse)

**Problem:**
- US: 2.14 contacts per URL
- GB: 2.59 contacts per URL

**Root Cause:**
- Possible HTML structure differences
- GPT extraction prompts may be tuned for UK-style pages
- Less critical than discovery/filtering

## Detailed Findings

### Discovery Phase Breakdown

**Universities with 0 candidates found (ALL 3 methods failed):**
- Albert Einstein College of Medicine
- Argonne National Laboratory
- Arizona State University
- Brown University
- California Institute of Technology
- Case Western Reserve University
- Columbia University
- Cornell University
- Harvard University
- Stanford University
- UC Berkeley
- UCLA
- University of Pennsylvania
- Yale University
- ... and 90+ more

**Method-specific failure rates:**
```
GB - Universities with 0 candidates found:
  Heuristic: 54/200 (27.0%)
  AI Slug:   54/200 (27.0%)
  AI Crawler: 54/200 (27.0%)

US - Universities with 0 candidates found:
  Heuristic: 139/200 (69.5%)
  AI Slug:   135/200 (67.5%)
  AI Crawler: 120/200 (60.0%)
```

### Filtering Phase Breakdown

**Filtering rates by method:**
```
GB - Percentage kept:
  Heuristic:  23.0% (152/662)
  AI Slug:    11.4% (613/5357)
  AI Crawler: 10.9% (584/5347)

US - Percentage kept:
  Heuristic:  2.7% (7/258)
  AI Slug:    3.8% (65/1716)
  AI Crawler: 3.3% (80/2426)
```

### Example Contact Quality Issues

**US (University of Washington - ai_crawler):**
- Names: "Workday\nStudent", "Jack Martin\nVice President", "Innovation\nFran"
- Generic roles: "Director", "Executive Director" (no "international" mention)
- Domain mismatch: uw.edu vs washington.edu (-2 penalty)
- Still scored 10+ (passed) due to "Director" bonus

**GB (University of Oxford - ai_crawler):**
- Names: "Professor Louise Richardson", "Dr. Jonathan Grant"
- Explicit roles: "Director of International Strategy", "Pro-Vice-Chancellor (Global Engagement)"
- Domain match: ox.ac.uk vs ox.ac.uk (no penalty)
- Scored 12 consistently

## Recommendations

### Priority 1: Fix Discovery for US Universities

**Action Items:**

1. **Add US-specific URL patterns to `config.py`:**
   ```python
   US_SLUGS = [
       "/about/leadership",
       "/about/administration", 
       "/administration/officers",
       "/president",
       "/provost",
       "/leadership",
       "/leadership/officers",
       "/administration/leadership",
       "/about/senior-leadership",
       "/executives",
       "/leadership-team",
       "/senior-management",
       "/board-trustees",  # Sometimes lists key officers
   ]
   ```

2. **Manually test 5-10 major US universities:**
   - Visit Harvard, Stanford, MIT, Berkeley, Yale
   - Document actual URL patterns for leadership/contact pages
   - Add discovered patterns to SLUGS

3. **Consider country-specific slug lists:**
   - Load different SLUGS based on domain (.edu vs .ac.uk)
   - Or merge US patterns into existing SLUGS list

### Priority 2: Relax Filtering for US Contacts

**Action Items:**

1. **Lower score threshold in `filtering.py`:**
   ```python
   # Current:
   return (score >= 6, score, "ok" if score >= 6 else "low score")
   
   # Proposed:
   threshold = 5 if ".edu" in email else 6  # More lenient for US
   return (score >= threshold, score, "ok" if score >= threshold else "low score")
   ```

2. **Adjust domain matching logic:**
   ```python
   # Handle common US abbreviations
   # e.g., "uw.edu" should match "washington.edu"
   # "mit.edu" should match "massachusetts.edu" (though MIT might not)
   ```

3. **Add US-specific role patterns:**
   ```python
   # Already has "global", "admissions", "engagement"
   # Consider adding:
   # - "worldwide"
   # - "external affairs"
   # - "institutional advancement"
   ```

### Priority 3: Improve Extraction Quality

**Action Items:**

1. **Review GPT prompts in `extraction.py`:**
   - Check for UK-specific assumptions
   - Test on sample US pages
   - Add US university examples to prompt if using few-shot

2. **Test extraction on specific US failures:**
   - Manually extract from Harvard, Stanford pages
   - Compare to GPT output
   - Identify gaps in extraction logic

## Quick Wins (Do First)

1. **Immediate: Lower threshold to 5** - One line change, instant improvement
2. **Quick: Add top 20 US URL patterns** - 10 minutes, massive impact
3. **Research: Visit 5 major US university sites** - Document URL patterns manually

## Testing Strategy

After implementing fixes:

```bash
# Test on small sample first
python benchmark_runner.py --country US --limit 20 --concurrent

# Compare before/after
python analyze_benchmarks.py

# If improved, run full benchmark
python benchmark_runner.py --country US --limit 200 --concurrent
```

## Files to Modify

1. **`gc_contacts/config.py`** - Add US_SLUGS patterns
2. **`gc_contacts/filtering.py`** - Lower threshold, adjust domain matching
3. **`gc_contacts/discovery.py`** - Review heuristic logic (if needed)
4. **`gc_contacts/extraction.py`** - Review GPT prompts (if needed)

## Data for Reference

**Analysis Scripts Created:**
- `analyze_benchmarks.py` - Overall comparison
- `root_cause_analysis.py` - Detailed breakdown
- `debug_filtering.py` - Contact-level filtering analysis

**Benchmark Runs:**
- GB: `benchmark_runs/GB_200unis_20251209_172632/`
- US: `benchmark_runs/US_200unis_20251210_082318/`

## Notes

- Role matching regex DOES work - tested and verified
- "Global", "admissions", "engagement" all match properly
- Main issue is NOT finding pages in the first place
- Secondary issue is scoring threshold too strict
- Extraction quality is acceptable (only 1.2x worse)

## Success Criteria

After fixes, target metrics:
- Discovery failure rate: < 35% (down from 66%)
- Filtering pass rate: > 8% (up from 3.5%)
- Average contacts per uni: > 1.5 (up from 0.25)
- Overall: Get within 3x of GB performance (currently 9x worse)
