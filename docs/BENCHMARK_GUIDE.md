# Benchmark: Comparing Discovery Methods

## Overview

This benchmark compares three methods for discovering candidate pages that contain contact information:

1. **Heuristic Slug Matching** - Hardcoded URL patterns
2. **AI Slug Inference** - GPT suggests URLs from homepage
3. **AI Crawler** - Full LLM-powered discovery and extraction

## What You'll Measure

### Primary Metric: RELEVANT CONTACTS

The key metric is **how many RELEVANT contacts** each method finds per university in reasonable time.

A "relevant contact" is one that:
- Has a university-affiliated email address
- Has a personal name (not generic "info@" etc.)
- Is associated with international/admissions/recruitment
- Passes the filtering rules (domain check, role scoring, etc.)

### Secondary Metrics

| Metric | What It Tells You |
|--------|------------------|
| **Success Rate** | % of universities that yielded >= 1 contact |
| **Avg Contacts/Uni** | Average relevant contacts per university |
| **Time/Uni** | How long it takes per university |
| **Cost/Uni** | How much it costs (in API tokens) |
| **Cost/Contact** | Cost to find one relevant contact |
| **Precision** | % of extracted contacts that pass filtering |

## The Three Methods Explained

### Method 1: Heuristic Slug Matching
**Cost:** $0.00  
**Speed:** ~1-2 seconds per university  
**Strategy:** Uses hardcoded patterns + navigation extraction

```
Discovery sources:
✓ Navigation/footer links (keyword matching)
✓ Sitemap URLs (keyword matching)
✓ Heuristic slugs (hardcoded patterns like /international, /contact, etc.)
✓ Subdomain variants (international.univ.edu, etc.)
```

**Pros:**
- Zero API cost
- Fast
- Deterministic

**Cons:**
- Limited to known patterns
- Misses unique institutional structures
- Can't adapt to each university's sitearchitecture

**Good for:** Budget-conscious, large-scale crawls

### Method 2: AI Slug Inference
**Cost:** ~$0.02-0.05 per university  
**Speed:** ~5-10 seconds per university  
**Strategy:** GPT reads homepage and suggests relevant URL paths

```
Discovery sources:
✓ Navigation/footer links (keyword matching)
✓ Sitemap URLs (keyword matching)
✓ AI-suggested slugs (GPT analyzes homepage) ← NEW
✓ Subdomain variants
```

**Pros:**
- Adapts to each university
- More targeted than pure heuristics
- Moderate cost

**Cons:**
- Still relies on discovered candidates + heuristics
- GPT might miss institution-specific patterns
- Moderate latency

**Good for:** Balanced approach - cost vs. accuracy

### Method 3: AI Crawler
**Cost:** ~$0.10-0.30 per university  
**Speed:** ~30-60 seconds per university  
**Strategy:** Combines AI slug inference + LLM contact extraction

```
Discovery sources:
✓ Navigation/footer links (keyword matching)
✓ Sitemap URLs (keyword matching)
✓ AI-suggested slugs (GPT analyzes homepage)
✓ Subdomain variants
✓ LLM extraction on candidate pages ← NEW
```

**Pros:**
- Most comprehensive
- LLM can understand complex contact info
- Best quality contacts

**Cons:**
- Highest cost
- Slowest (multiple LLM calls)
- May hallucinate contacts

**Good for:** Quality over cost

## How to Run the Benchmark

### 1. Basic Benchmark (5 universities, all methods)

```bash
python benchmark_runner.py --country GB --limit 5
```

This will:
- Fetch 5 UK universities from OpenAlex
- Test all 3 methods on each
- Save results to `benchmark_results.csv`
- Print summary

### 2. Compare Just Two Methods (10 universities)

```bash
python benchmark_runner.py \
  --country US \
  --limit 10 \
  --methods heuristic ai_slug
```

### 3. Test with Different Parameters

```bash
python benchmark_runner.py \
  --country CN \
  --limit 20 \
  --methods heuristic ai_slug ai_crawler \
  --probe-max 15 \
  --output results_china.csv \
  --ignore-robots
```

### 4. Analyze Results

```bash
python analyze_benchmark.py benchmark_results.csv
```

This generates a detailed analysis with:
- Per-method statistics
- Success rates
- Cost analysis
- Time analysis
- Quality metrics
- Rankings and recommendations

## Output Files

### `benchmark_results.csv`

Raw results, one row per (method, university) combination:

| Column | Meaning |
|--------|---------|
| method | heuristic, ai_slug, or ai_crawler |
| university | University name |
| candidates_found | Total unique URLs discovered |
| candidates_probed | How many were actually fetched |
| contacts_extracted | Total contacts found (before filtering) |
| contacts_kept | Contacts that passed filtering |
| time_seconds | Total time for this method on this uni |
| tokens_in | Input tokens used |
| tokens_out | Output tokens used |
| cost_dollars | Cost in USD |
| best_url | Which URL had the best contacts |

### Analysis Output

The `analyze_benchmark.py` script prints:

1. **Summary Statistics** - By method aggregates
2. **Success Rate** - % of universities with >= 1 contact
3. **Cost Analysis** - Total and per-contact costs
4. **Time Analysis** - Speed and efficiency
5. **Quality Metrics** - Precision and extraction rates
6. **Rankings** - Weighted scores for each method
7. **Per-University Breakdown** - How each method performed on each university

## Interpreting Results

### Example Output

```
METHOD COMPARISON REPORT
========================================

HEURISTIC:
  Universities tested:        10
  Success rate:               70.0%
  Total contacts kept:        45
  Avg contacts per uni:       4.5
  Avg time per uni:           1.2s
  Total cost:                 $0.00
  Avg cost per uni:           $0.0000

AI_SLUG:
  Universities tested:        10
  Success rate:               80.0%
  Total contacts kept:        62
  Avg contacts per uni:       6.2
  Avg time per uni:           7.3s
  Total cost:                 $0.32
  Avg cost per uni:           $0.0320

AI_CRAWLER:
  Universities tested:        10
  Success rate:               85.0%
  Total contacts kept:        71
  Avg contacts per uni:       7.1
  Avg time per uni:           45.2s
  Total cost:                 $1.80
  Avg cost per uni:           $0.1800
```

**Analysis:**
- AI_SLUG found 38% more contacts than HEURISTIC, at cost of $0.032/uni
- AI_CRAWLER found only 15% more than AI_SLUG, but costs 5.6x more
- **Recommendation:** AI_SLUG offers best balance

## Cost Considerations

### Token Pricing

- **Input tokens:** $0.60 per 1M tokens
- **Output tokens:** $2.40 per 1M tokens

Example:
- GPT call generates 300 tokens in, 600 tokens out
- Cost = (300 / 1M) * $0.60 + (600 / 1M) * $2.40 = $0.0018

### Scaling Example

If crawling 1,000 universities:

| Method | Cost | Time |
|--------|------|------|
| Heuristic | $0 | ~20 min |
| AI Slug | $32 | ~2 hours |
| AI Crawler | $180 | ~12 hours |

## Optimization Tips

### To Reduce Costs

1. Use **Heuristic** for initial discovery, then validate with sample
2. Only use **AI methods** on universities you're unsure about
3. Reduce `--probe-max` to probe fewer candidates
4. Use `--methods heuristic` for large-scale crawls

### To Improve Accuracy

1. Use **AI_CRAWLER** on subset for quality
2. Combine methods: Use heuristic to find candidates, then LLM to extract
3. Tune filtering rules based on results
4. Manual validation on sample

### To Balance Cost/Quality

**Recommended approach:**
- Start with **Heuristic** to get baseline
- Use **AI_SLUG** for 20-30% of universities where heuristic found no contacts
- Use **AI_CRAWLER** only for critical/high-profile institutions

## Next Steps After Benchmark

### 1. Choose Best Method

Based on your metrics, select the method that best fits:
- Your budget
- Your time constraints
- Your accuracy requirements

### 2. Run Full Crawl

```bash
# If you chose heuristic
python gc_contacts_cli.py GB --outfile results.csv

# If you want to add AI slug discovery
# (This requires modifying main.py to use discover_ai_slug)
```

### 3. Iterate

- If results aren't good enough, try better filtering
- If too many false positives, increase score threshold
- If too slow, use heuristic-only for large datasets

## Example Hypothesis Testing

### Hypothesis 1: "AI Slug finds better contacts"

```bash
# Test on 20 universities
python benchmark_runner.py --country DE --limit 20 \
  --methods heuristic ai_slug --output hypothesis1.csv
python analyze_benchmark.py hypothesis1.csv
# Check: Is contacts_kept higher for AI_SLUG?
```

### Hypothesis 2: "Navigation links are best source"

Modify `benchmark.py` to track `source_breakdown` per method, then analyze which source type yields best contacts.

### Hypothesis 3: "Larger universities have more contacts"

Add university size to benchmark, then correlate with contacts_kept.

## Troubleshooting

### "No universities found"

```bash
# Check your country code
python -c "from gc_contacts.openalex import fetch_openalex_unis; \
  import asyncio; \
  asyncio.run(fetch_openalex_unis('INVALID'))"
```

### "Cost is very high"

- Reduce `--limit` to test fewer universities
- Use `--probe-max 5` to probe fewer candidates
- Test `--methods heuristic` first

### "Results seem wrong"

- Run `--ignore-robots` to avoid robots.txt blocking
- Check logs for errors: `python benchmark_runner.py ... 2>&1 | grep ERROR`
- Manually inspect a university's contacts

## Questions to Answer

After running the benchmark, you should be able to answer:

1. **Which method finds the most relevant contacts?** ← Primary metric
2. **Which method is fastest?** ← Speed efficiency
3. **Which method is cheapest?** ← Cost efficiency
4. **What's the cost per relevant contact?** ← True efficiency
5. **How does success rate vary by method?** ← Reliability
6. **Is there a method that's best for your use case?** ← Decision making

## Recommendation Framework

| If You Care About | Choose |
|-------------------|--------|
| Cost | Heuristic |
| Speed | Heuristic |
| Accuracy | AI Crawler |
| Balance | AI Slug |
| Scale (1000+ unis) | Heuristic |
| Quality (100 unis) | AI Crawler |
| Research quality | AI Slug (best ROI) |

---

**Now run the benchmark and discover which method works best for YOUR data!**
