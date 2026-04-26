# Quick Start: Benchmark the Three Discovery Methods

Now that the benchmark saves actual contact data, here's how to use it to compare methods.

## TL;DR

```bash
# Run benchmark for 20 UK universities
python benchmark_runner.py --country GB --limit 20 --contacts-dir benchmark_contacts

# Analyze results
python analyze_contacts.py --dir benchmark_contacts

# Check statistics
cat benchmark_results.csv
```

## Step-by-Step

### Step 1: Run the Benchmark
```bash
python benchmark_runner.py \
  --country GB \
  --limit 20 \
  --methods heuristic ai_slug ai_crawler \
  --probe-max 10 \
  --output benchmark_results.csv \
  --contacts-dir benchmark_contacts
```

**Parameters:**
- `--country GB` - ISO country code (GB, US, CN, etc.)
- `--limit 20` - Number of universities to test (20-50 recommended for statistical validity)
- `--methods` - Which methods to compare (default: all three)
- `--probe-max 10` - Max candidate pages to check per method per university
- `--output` - CSV file for statistics
- `--contacts-dir` - Directory for contact JSON files

**What happens:**
- Fetches 20 universities from OpenAlex
- For each university, tries all 3 discovery methods
- For each method, probes up to 10 candidate pages
- Extracts contacts using regex + LLM
- Saves statistics to CSV
- Saves actual contacts to JSON files

**Runtime:** ~2-3 minutes per university × number of methods = 5-10 minutes total for 20 universities

### Step 2: Analyze Aggregate Statistics
```bash
# View CSV with column headers
cat benchmark_results.csv | head -20

# Or use Python to analyze
python analyze_contacts.py --dir benchmark_contacts
```

This shows:
- Total contacts found per method
- Average time per university
- Success rate (% of universities with ≥1 contact)
- Cost per method

### Step 3: Inspect Actual Contacts

#### Overview of all contacts by method
```bash
python analyze_contacts.py --dir benchmark_contacts
```

Shows summary + sample contacts for each method.

#### Detailed contacts for one method
```bash
python analyze_contacts.py --dir benchmark_contacts --method heuristic
```

Shows all contacts found by heuristic method.

#### View raw JSON
```bash
# List all contact files
ls benchmark_contacts/

# View specific method's contacts
cat benchmark_contacts/heuristic_university_of_oxford_contacts.json | jq .contacts

# View first 3 contacts from each method
jq '.contacts[0:3]' benchmark_contacts/heuristic_*.json
```

## Interpreting Results

### Key Metrics to Compare

| Metric | What It Means | Formula |
|--------|---------------|---------|
| **Total contacts kept** | How many valid contacts found | Across all universities |
| **Success rate** | % universities with ≥1 contact | (n_success / total_unis) × 100 |
| **Avg contacts/uni** | Average per-university yield | total_contacts / num_universities |
| **Time per uni** | Speed of discovery | seconds |
| **Cost per uni** | Dollar cost (api tokens) | tokens / 1M × cost per million |
| **Precision** | % of found contacts valid | kept_count / extracted_count |

### Decision Framework

Choose the method that maximizes: **Relevant Contacts ÷ Time × Cost**

**Heuristic** typically:
- ✅ Fast (1-3 sec/uni)
- ✅ Free (no API cost)
- ❌ Fewer contacts (limited patterns)

**AI Slug** typically:
- ⚠️ Moderate speed (3-5 sec/uni)
- ⚠️ Low cost ($0.02-0.04/uni)
- ⚠️ Decent coverage

**AI Crawler** typically:
- ❌ Slow (4-7 sec/uni)
- ❌ Higher cost ($0.10-0.20/uni)
- ✅ More contacts potentially

### Questions to Ask

1. **Coverage**: Which method finds contacts on most universities?
   - Look at success_rate in summary

2. **Quality**: Are the contacts actually valid?
   - Inspect 10-20 contacts per method
   - Check if names look real, emails valid, roles relevant
   - Look for duplicates or near-duplicates

3. **Cost**: Which method has best ROI?
   - Compare contacts_kept / cost_dollars

4. **Speed**: How long can we wait?
   - If processing 10,000 universities, time multiplies
   - Heuristic: 10-30k seconds → 3-8 hours
   - AI methods: 40-70k seconds → 11-20 hours

## Example Analysis Session

```bash
# Run benchmark
$ python benchmark_runner.py --country GB --limit 20 --contacts-dir benchmark_contacts
[INFO] Fetching universities from GB...
[INFO] Found 20 universities
[INFO] Running benchmark with methods: heuristic, ai_slug, ai_crawler
[INFO] [1/20] Testing University of Oxford
  heuristic... → 5 relevant contacts found
  ai_slug... → 8 relevant contacts found
  ai_crawler... → 11 relevant contacts found
...
[INFO] Results saved to benchmark_results.csv
[INFO] Contact details saved to benchmark_contacts/

# Analyze results
$ python analyze_contacts.py --dir benchmark_contacts

============================================================
CONTACT DATA ANALYSIS
============================================================

HEURISTIC:
  Universities processed: 20
  Total contacts found:   47
  Avg per university:     2.4
  Sample contacts:
    • Dr. Jane Smith <jane@oxford.ac.uk> (Professor) from https://www.ox.ac.uk/staff
    • Prof. John Brown <j.brown@oxford.ac.uk> (Dean) from https://www.ox.ac.uk/leadership
    • ...

AI_SLUG:
  Universities processed: 20
  Total contacts found:   89
  Avg per university:     4.5
  Sample contacts:
    • Dr. Sarah Johnson <s.johnson@oxford.ac.uk> (Researcher) from https://www.ox.ac.uk/people
    • ...

AI_CRAWLER:
  Universities processed: 20
  Total contacts found:   156
  Avg per university:     7.8
  Sample contacts:
    • Dr. Michael Chen <m.chen@oxford.ac.uk> (Senior Lecturer) from https://www.ox.ac.uk/dept/engineering
    • ...

============================================================

# Decision: AI Crawler found 3x more contacts than heuristic
# Cost it on larger sample to confirm statistically significant
```

## Troubleshooting

### No contacts found
- Try increasing `--limit` to more universities
- Check `--probe-max` is set to reasonable value (10-20)
- Verify `--ignore-robots` if needed
- Check logs for errors (looks for "LLM extraction failed")

### High cost
- Reduce `--limit` to fewer universities
- Reduce `--probe-max` to fewer candidates per university
- Use `--methods heuristic` first to get baseline

### Slow execution
- Use `--limit 5` for quick test run first
- Can run different methods separately:
  ```bash
  python benchmark_runner.py --country GB --limit 20 --methods heuristic
  # Run later
  python benchmark_runner.py --country GB --limit 20 --methods ai_slug
  ```

### Memory issues
- Reduce `--limit` (fewer universities loaded at once)
- Each university/method combination creates one JSON file

## Next: Production Decision

Once you've:
1. ✅ Run on 20-50 universities
2. ✅ Analyzed aggregate statistics
3. ✅ Manually reviewed 30-50 contacts per method
4. ✅ Confirmed which method has best quality/cost ratio

**Decision options:**
1. Use single best method for production
2. Combine methods (e.g., heuristic + AI fallback)
3. Run A/B test on real universities
4. Integrate into main gc_contacts/main.py

## Files Reference

| File | Purpose |
|------|---------|
| `benchmark_runner.py` | Run the benchmark |
| `analyze_contacts.py` | Analyze contact data |
| `benchmark_results.csv` | Statistics for each method |
| `benchmark_contacts/*.json` | Actual contact details |
| `gc_contacts/benchmark.py` | Core benchmark logic |

Questions? Check:
- `BENCHMARK_CONTACTS_EXPORT.md` - Technical details
- `BENCHMARK_IMPLEMENTATION_SUMMARY.md` - Implementation notes
- `BENCHMARK_GUIDE.md` - Original comprehensive guide
