# Quick Start: Benchmarking Discovery Methods

## Your Three Methods

| Method | Cost | Speed | When to Use |
|--------|------|-------|------------|
| **Heuristic** | $0 | ~1-2s/uni | Large-scale, budget-conscious |
| **AI Slug** | $0.03/uni | ~5-10s/uni | Balanced approach (RECOMMENDED) |
| **AI Crawler** | $0.15/uni | ~30-60s/uni | Quality-focused |

## Quick Benchmark (5 minutes)

```bash
# Test on 5 UK universities (all methods)
python benchmark_runner.py --country GB --limit 5

# Analyze results
python analyze_benchmark.py benchmark_results.csv
```

Output: `benchmark_results.csv` + detailed analysis

## What Gets Compared

### Metric: **Most RELEVANT Contacts in Reasonable Time**

For each method, measures:
- ✅ **Contacts found** - How many passed filtering
- ✅ **Success rate** - % of universities with >= 1 contact
- ✅ **Time/cost** - How fast and cheap
- ✅ **Quality** - Precision of extractions

## Run Full Benchmark

```bash
# 20 universities, all methods, save detailed results
python benchmark_runner.py \
  --country GB \
  --limit 20 \
  --methods heuristic ai_slug ai_crawler \
  --output results.csv

# Analyze
python analyze_benchmark.py results.csv
```

## Example Results

```
HEURISTIC:  45 contacts, $0.00, 4.5/uni
AI_SLUG:    62 contacts, $0.32, 6.2/uni  ← 38% more, small cost
AI_CRAWLER: 71 contacts, $1.80, 7.1/uni  ← 15% more, 5.6x cost
```

**Interpretation:** AI_SLUG is best value for research

## Customize Benchmark

```bash
# Test fewer candidates per method (cheaper)
python benchmark_runner.py --country GB --limit 10 --probe-max 5

# Test specific methods
python benchmark_runner.py --country US --limit 15 \
  --methods heuristic ai_slug

# Different country
python benchmark_runner.py --country CN --limit 20

# Ignore robots.txt (faster but less ethical)
python benchmark_runner.py --country DE --limit 10 --ignore-robots
```

## Output: `benchmark_results.csv`

One row per (method, university):

```
method,university,candidates_found,contacts_extracted,contacts_kept,time_seconds,cost_dollars
heuristic,University of Oxford,87,12,8,1.2,0.0000
ai_slug,University of Oxford,112,18,11,7.5,0.0250
ai_crawler,University of Oxford,112,25,14,45.0,0.1800
heuristic,MIT,54,9,6,1.1,0.0000
ai_slug,MIT,78,14,9,6.2,0.0210
ai_crawler,MIT,78,22,13,42.0,0.1650
```

## Analysis Output (from `analyze_benchmark.py`)

Shows:
1. Summary statistics by method
2. Success rates
3. Cost analysis (total + per-contact)
4. Time analysis
5. Quality/precision metrics
6. **Rankings** (which method is best)
7. Per-university breakdown

## Making Your Decision

### Questions to Answer

```
1. How many total relevant contacts did each method find?
   → Look at "total_contacts_kept" in analysis

2. What's the success rate (% of unis with >= 1 contact)?
   → Heuristic: X%, AI_Slug: Y%, AI_Crawler: Z%

3. How much does each relevant contact cost?
   → Total cost / total contacts

4. Is the quality difference worth the cost?
   → Compare precision and contacts_kept
```

### Example Decision Tree

```
Does heuristic find enough contacts?
├─ YES → Use HEURISTIC (save money)
└─ NO (< 5 contacts/uni)
   └─ Try AI_SLUG
      ├─ Better? (10%+ improvement)
      │  └─ Use AI_SLUG
      └─ Same? (< 5% improvement)
         └─ Stick with HEURISTIC
```

## Next: Run Full Crawl with Best Method

Once you know which method is best:

```bash
# Use in main crawler (heuristic is default)
python gc_contacts_cli.py GB --outfile results.csv

# To use AI methods, modify main.py to call:
# - discover_ai_slug() instead of gather_candidates()
# - discover_ai_crawler() instead of gather_candidates()
```

---

**Run the benchmark now, see which method wins for YOUR data!** 🚀
