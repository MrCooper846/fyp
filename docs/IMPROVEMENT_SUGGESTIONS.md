# System-Wide Improvement Suggestions

**Date:** December 10, 2025  
**Context:** Post-benchmark analysis, after identifying US vs GB performance gaps

---

## 1. 🔄 Caching Strategy Issues

### Problem
System makes expensive GPT calls with minimal caching. Every re-run costs the same as the first run.

### Current State
- Some basic caching in `.cache/` directory
- No TTL or invalidation strategy
- Cache is per-process, not shared
- Re-running same universities = full cost again

### Recommendations
- **Cache discovery results** (candidate URLs per homepage) - rarely change
- **Cache extraction results** per URL - same page → same contacts
- **Add TTL-based invalidation** (e.g., 30 days for university pages)
- **Shared cache** across benchmark runs using SQLite or Redis
- **Cache hit rate metrics** to measure savings

### Expected Impact
- 80%+ cost reduction on re-runs
- Faster benchmarking iterations
- Better debugging (can replay without API calls)

### Implementation Effort
- Low: 3-4 hours
- Files: New `gc_contacts/cache.py`, modify `discovery.py`, `extraction.py`

---

## 2. 📊 Rate Limiting is Per-Process Only

### Problem
Token bucket in `config.py` resets on every process restart. Multiple concurrent processes will independently hit OpenAI rate limits.

### Current State
```python
# config.py
bucket_lock = asyncio.Lock()
bucket_used = 0
bucket_reset = time.monotonic()
```

### Recommendations
- **Redis-based rate limiting** for cross-process coordination
- **Or: Use OpenAI SDK's built-in rate limit handling** with automatic retries
- **Track costs in real-time** across all processes
- **Cost alerts** when approaching budget limits
- **Per-API-key tracking** if using multiple keys

### Expected Impact
- No unexpected rate limit errors
- Better resource utilization
- Cost visibility

### Implementation Effort
- Medium: 4-5 hours
- Files: `gc_contacts/rate_limiter.py` (new), modify `config.py`

---

## 3. 🔁 No Retry Logic for Failed Universities

### Problem
Benchmarks show many timeouts and failures. Once a university fails, it's lost. No second chances.

### Current State
- Single attempt per university
- Timeout = complete loss
- No distinction between temporary vs permanent failures

### Recommendations
- **Exponential backoff retry** for HTTP failures (3 attempts)
- **Fallback methods** - if heuristic fails, try AI methods
- **Failure classification** - temporary (timeout) vs permanent (404)
- **Resume capability** - save progress, restart from last success
- **Failed universities log** for manual investigation

### Expected Impact
- 20-30% more successful discoveries
- Better benchmark completion rates
- Can survive network hiccups

### Implementation Effort
- Medium: 5-6 hours
- Files: New `gc_contacts/retry.py`, modify `benchmark.py`, `main.py`

---

## 4. 🔍 Discovery Methods Could Be Smarter

### Problem
Current heuristic generates ~500+ possible URLs and tries them blindly. Most fail. Very inefficient.

### Current Approach
```python
# Generate all combinations of PREFIXES + TOKENS
SLUGS = [""] + [f"/{p}/{t}" for p in PREFIXES for t in TOKENS]
# Try all ~500 URLs, most 404
```

### Smarter Approaches

#### A. Sitemap.xml Crawling (HIGHEST IMPACT)
```python
# Most universities publish sitemap.xml
urls = parse_sitemap("https://university.edu/sitemap.xml")
candidates = [u for u in urls if matches_keywords(u, ["international", "leadership"])]
```
- **Pros:** Official list of all pages, guaranteed valid URLs
- **Cons:** Not all sites have sitemaps
- **Impact:** 50-70% better discovery rate
- **Effort:** Low (2-3 hours)

#### B. Search Engine API
```python
# Use Bing/Google to find relevant pages
results = search_api(f"site:harvard.edu 'international admissions director'")
```
- **Pros:** Finds pages even with weird URL structures
- **Cons:** Requires API key, costs money, rate limited
- **Impact:** 80%+ discovery rate
- **Effort:** Medium (4-5 hours + API setup)

#### C. Breadcrumb Following
```python
# Start from homepage, follow actual navigation links
links = extract_nav_links(homepage)
candidates = [l for l in links if looks_promising(l)]
```
- **Pros:** Follows real user paths, finds nested pages
- **Cons:** Slower (multiple hops), can miss pages
- **Impact:** 40-60% better discovery
- **Effort:** Medium (5-6 hours)

#### D. Machine Learning on URL Patterns
```python
# Learn from successful URLs
model = train_on_successful_patterns()
candidates = model.predict_likely_urls(homepage)
```
- **Pros:** Auto-adapts to different countries/structures
- **Cons:** Requires training data, complex
- **Impact:** 60-80% better discovery
- **Effort:** High (20+ hours)

### Recommendation Priority
1. **Sitemap.xml** - Quick win, huge impact
2. **Search API** - Best discovery rate, requires budget
3. **Breadcrumb** - Good middle ground
4. **ML** - Long-term investment

### Implementation Effort
- Sitemap: Low (2-3 hours)
- Search: Medium (4-5 hours)
- Breadcrumb: Medium (5-6 hours)
- ML: High (20+ hours)

---

## 5. 🤖 Filtering Could Use ML Instead of Hard Rules

### Problem
Score threshold of 6 is arbitrary. Doesn't adapt to US vs GB differences. Lots of manual tuning needed.

### Current Approach
```python
score = role_score(role)
if not ok_domain: score -= 2
if re.search(INTL_HINTS, role): score += 2
return (score >= 6, score, reason)  # Hard threshold!
```

### ML Approach
```python
# Train classifier on labeled data
features = extract_features(contact)  # role keywords, domain, page context
probability = classifier.predict_proba(features)
return (probability > 0.7, probability, "ML score")
```

### Features to Extract
- Role keyword presence/count
- Email domain match
- Page URL context (contains "international"?)
- Name quality (proper capitalization, length)
- Title seniority level
- Contact type (individual vs department)

### Training Data Sources
- Your current filtered contacts (auto-labeled by score)
- Manual review of borderline cases (score 5-7)
- Known good contacts from successful runs
- Known bad contacts (generic inboxes, wrong people)

### Models to Try
1. **Logistic Regression** - Simple, interpretable baseline
2. **Random Forest** - Better accuracy, still interpretable
3. **XGBoost** - Best accuracy, harder to interpret

### Expected Impact
- Auto-adapts to US vs GB
- Better precision/recall
- Confidence scores for ranking
- Can improve over time with more data

### Implementation Effort
- Medium: 8-10 hours
- Files: New `gc_contacts/ml_filter.py`, training script

---

## 6. 📈 No Quality Metrics in Dashboard

### Problem
Dashboard shows counts but can't assess contact quality. Can't identify which contacts are reliable vs questionable.

### Current Dashboard
- Total contacts kept
- Method comparison
- University breakdown
- **Missing:** Quality indicators, confidence, suspicious patterns

### Recommended Additions

#### A. Confidence Scores
```javascript
// Show per-contact confidence
contacts.forEach(c => {
  c.confidence = calculateConfidence(c);  // 0-100%
  c.badge = c.confidence > 80 ? "high" : c.confidence > 50 ? "medium" : "low";
});
```

#### B. Quality Flags
- 🚩 Generic email (info@, contact@)
- ⚠️ Domain mismatch
- ⚠️ Borderline score (6-7)
- ⚠️ Suspicious name pattern
- ✓ Explicit international role

#### C. Manual Review UI
```javascript
// Add "Mark as good/bad" buttons
// Save labels to training data
// Use for ML model improvement
```

#### D. Quality Metrics Panel
```
Overall Quality Score: 78/100
High Confidence: 45 contacts (75%)
Medium Confidence: 12 contacts (20%)
Low Confidence: 3 contacts (5%)
Flagged for Review: 8 contacts
```

### Expected Impact
- Better trust in results
- Easy identification of problems
- Training data for ML models
- Actionable insights

### Implementation Effort
- Medium: 6-8 hours
- Files: Modify `view_benchmark_contacts.html`, add scoring logic

---

## 7. ⚡ Parallel Processing Inefficiencies

### Problem
Even with `--concurrent`, bottlenecks limit throughput.

### Current Bottlenecks
1. **Single OpenAI API key** - shared rate limit
2. **No request batching** - each contact = separate API call
3. **Sequential GPT calls** per university
4. **No batch API usage** - missing 50% cost discount

### Optimizations

#### A. OpenAI Batch API
```python
# Instead of real-time calls
response = await oai.chat.completions.create(...)

# Use batch API (50% cheaper, 24hr turnaround)
batch = await oai.batches.create(requests=bulk_requests)
# Wait for completion, retrieve results
```
- **Pros:** 50% cost savings
- **Cons:** 24hr delay, not suitable for interactive use
- **Best for:** Large benchmark runs

#### B. Multiple API Keys
```python
# Distribute load across multiple keys
keys = [key1, key2, key3]
key = random.choice(keys)
response = await oai_clients[key].chat.completions.create(...)
```
- **Pros:** 3x throughput
- **Cons:** More complex, more cost
- **Best for:** Time-sensitive large runs

#### C. Request Batching
```python
# Batch multiple extractions in one GPT call
prompt = "Extract contacts from these 5 pages: [page1, page2, ...]"
# Parse out results for each page
```
- **Pros:** Fewer API calls, lower cost
- **Cons:** Harder to parse, quality may suffer
- **Best for:** Similar pages from same university

### Expected Impact
- 2-3x faster benchmarks (multiple keys)
- 50% cost reduction (batch API)
- Better throughput utilization

### Implementation Effort
- Batch API: Medium (6-8 hours)
- Multiple keys: Low (2-3 hours)
- Request batching: High (10+ hours, tricky parsing)

---

## 8. 🧪 No A/B Testing Framework

### Problem
When you change SLUGS, prompts, or filtering rules, you can't measure if it actually helped.

### Current Process
1. Change code
2. Run benchmark
3. Eyeball results
4. Hope it's better?

### Proper A/B Testing

#### A. Experiment Tracking
```python
experiment = {
    "id": "us_slugs_v2",
    "config_hash": hash(config),
    "slugs_version": "2.0",
    "threshold": 5,
    "timestamp": now()
}
```

#### B. Side-by-Side Comparison
```python
# Run with config A
results_a = benchmark(config_a)

# Run with config B
results_b = benchmark(config_b)

# Statistical comparison
improvement = compare_results(results_a, results_b)
print(f"Config B is {improvement.percent}% better (p={improvement.p_value})")
```

#### C. Version Control for Prompts
```python
prompts/
  extraction_v1.txt
  extraction_v2.txt
  extraction_v3.txt
  
# Track which version used in each run
run_metadata["prompt_version"] = "extraction_v2"
```

### Expected Impact
- Data-driven decisions
- Confidence in changes
- Avoid regressions
- Historical tracking

### Implementation Effort
- Low-Medium: 4-6 hours
- Files: New `gc_contacts/experiments.py`, modify runners

---

## 9. 💬 GPT Prompts Not Optimized

### Problem
Current prompts may not be optimal for accuracy, cost, or consistency.

### Optimization Strategies

#### A. Few-Shot Examples
```python
# Current (zero-shot)
prompt = "Extract contacts from this page: {html}"

# Better (few-shot)
prompt = """
Extract contacts from this page.

Example 1:
Input: <html>Dr. Jane Smith, Director of International Admissions...</html>
Output: [{"name": "Dr. Jane Smith", "role": "Director of International Admissions", ...}]

Example 2:
Input: <html>Prof. John Doe, Vice Chancellor...</html>
Output: [{"name": "Prof. John Doe", "role": "Vice Chancellor", ...}]

Now extract from: {html}
"""
```

#### B. Chain-of-Thought
```python
prompt = """
Extract contacts and explain your reasoning.

For each contact, think through:
1. Is this person's role relevant to international admissions?
2. Is this a personal email or generic inbox?
3. Does the role indicate seniority/authority?

{html}
"""
```

#### C. Structured Output (JSON Mode)
```python
# Use GPT's JSON mode for reliable parsing
response = await oai.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"}  # Guarantees valid JSON
)
```

#### D. Temperature Tuning
```python
# Current: Default temperature (1.0?)
# Better: Lower for consistent extraction
temperature=0.3  # More deterministic, less creative
```

#### E. Model Comparison
```python
# Test different models on same task
models = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
for model in models:
    results[model] = benchmark_with_model(model)
# Compare accuracy vs cost
```

### Expected Impact
- 10-30% better extraction accuracy
- More consistent results
- Lower parsing errors
- Possibly lower costs (fewer retries)

### Implementation Effort
- Few-shot: Low (1-2 hours)
- JSON mode: Low (1 hour)
- Temperature: Low (30 min)
- Model comparison: Medium (3-4 hours)

---

## 10. ✅ Missing Validation Layers

### Problem
Contacts are extracted and filtered, but not validated for actual correctness.

### Validation Checks

#### A. Email Existence (SMTP Validation)
```python
import smtplib
def email_exists(email):
    domain = email.split('@')[1]
    mx_records = dns.resolver.resolve(domain, 'MX')
    # Connect to mail server and verify
    # WARNING: Slow, can be blocked
```
- **Pros:** Catches fake emails
- **Cons:** Very slow (seconds per email), often blocked
- **Recommendation:** Use sparingly, maybe only for high-value contacts

#### B. DNS MX Record Check (Fast)
```python
import dns.resolver
def domain_valid(email):
    domain = email.split('@')[1]
    try:
        mx = dns.resolver.resolve(domain, 'MX')
        return len(mx) > 0
    except:
        return False
```
- **Pros:** Fast (milliseconds), reliable
- **Cons:** Doesn't guarantee email exists
- **Recommendation:** Use for all contacts

#### C. Duplicate Detection
```python
# Check for duplicates within same university
seen_emails = set()
for contact in contacts:
    if contact['email'] in seen_emails:
        contact['duplicate'] = True
    seen_emails.add(contact['email'])
```

#### D. Name-Role Consistency
```python
# Flag if role appears in name field (extraction error)
if any(word in contact['name'].lower() for word in ['director', 'head', 'manager']):
    contact['suspicious_name'] = True
```

#### E. HTTP Status Validation
```python
# Check if page_url actually exists
async def validate_page(url):
    response = await http.head(url)
    return response.status_code == 200
```

### Expected Impact
- Higher quality final contacts
- Fewer embarrassing false positives
- Better downstream usage

### Implementation Effort
- MX check: Low (1 hour)
- Duplicates: Low (30 min)
- Name validation: Low (1 hour)
- SMTP validation: Medium (3-4 hours, complex)

---

## 11. 📡 No Monitoring/Alerting

### Problem
Long benchmark runs fail silently. No visibility into progress, costs, or issues.

### Monitoring Solutions

#### A. Progress Webhooks
```python
# Discord webhook
async def notify_progress(message):
    await webhook.send(f"🎓 Benchmark Update: {message}")

# Send updates at milestones
notify_progress(f"25% complete - {found} contacts found so far")
notify_progress(f"⚠️ High failure rate: {fail_rate}%")
notify_progress(f"✅ Benchmark complete! Total cost: ${cost}")
```

#### B. Cost Tracking
```python
# Real-time cost accumulation
current_cost = 0
cost_threshold = 50.00  # Alert if > $50

if current_cost > cost_threshold:
    notify_alert(f"🚨 Cost exceeded ${cost_threshold}!")
    # Optionally: abort run
```

#### C. Failure Rate Monitoring
```python
# Abort if too many failures
if failure_rate > 0.80:  # 80% failing
    notify_alert("🚨 High failure rate - aborting!")
    raise Exception("Too many failures")
```

#### D. Performance Degradation
```python
# Track speed over time
avg_time_per_uni = total_time / unis_processed
if avg_time_per_uni > 120:  # > 2 minutes per uni
    notify_alert("⚠️ Benchmark running slow")
```

### Integration Options
- Discord webhooks (easy)
- Slack webhooks (easy)
- Email notifications (medium)
- Telegram bot (medium)
- Custom dashboard with WebSockets (hard)

### Expected Impact
- Know when benchmarks fail
- Catch cost overruns early
- Better debugging of issues
- Peace of mind on long runs

### Implementation Effort
- Discord/Slack: Low (2-3 hours)
- Email: Low (2 hours)
- Advanced dashboard: High (15+ hours)

---

## 12. 📊 Benchmark Results Not Actionable

### Problem
You know US performs worse, but don't know WHY at a granular level.

### Deeper Analytics

#### A. URL Pattern Analysis
```python
# Which URL patterns actually work?
successful_urls = results[results['contacts_kept'] > 0]['best_url']
patterns = extract_patterns(successful_urls)

# Output:
# /about/leadership: 45% success rate
# /international: 78% success rate
# /admissions: 23% success rate
```

#### B. University Difficulty Clustering
```python
# Group universities by "difficulty"
easy = universities where any method succeeds (>5 contacts)
medium = universities where only AI methods succeed (1-5 contacts)
hard = universities where all methods fail (0 contacts)

# What makes a university "hard"?
# - Domain structure
# - CMS type (WordPress, Drupal, custom)
# - Website complexity
```

#### C. Correlation Analysis
```python
# What predicts success?
correlations = {
    'has_sitemap': 0.65,  # Strong predictor
    'wordpress_site': 0.45,
    'domain_age': -0.12,  # Older sites harder?
    'page_load_time': -0.23,
}
```

#### D. Method Effectiveness by Context
```python
# Which method works best when?
# Heuristic: Best for UK universities with standard structure
# AI Slug: Best for modern websites with clean HTML
# AI Crawler: Best for complex sites with nested navigation
```

### Visualization Ideas
- Heatmap of success rate by country + method
- URL pattern success tree diagram
- University difficulty distribution
- Cost vs accuracy tradeoff curves

### Expected Impact
- Understand what works and why
- Prioritize improvements
- Predict success before running
- Better method selection

### Implementation Effort
- Medium: 6-8 hours for basic analytics
- High: 15+ hours for advanced visualizations

---

## Quick Wins (Impact vs Effort Matrix)

### Highest ROI (Do First)
1. ✅ **Sitemap.xml crawling** - 2-3h work, 50%+ discovery boost
2. ✅ **Lower threshold to 5** - 1min, immediate US improvement  
3. ✅ **Add US URL patterns** - 30min, big discovery boost
4. ✅ **Cache GPT responses** - 3-4h, 80% cost savings on reruns
5. ✅ **Few-shot prompts** - 1-2h, better extraction quality

### High ROI (Do Soon)
6. **Retry logic** - 5-6h, 20-30% more successes
7. **MX record validation** - 1h, filter bad emails
8. **JSON mode for GPT** - 1h, more reliable parsing
9. **Progress webhooks** - 2-3h, visibility into runs
10. **Duplicate detection** - 30min, cleaner results

### Medium ROI (Nice to Have)
11. **Search API integration** - 4-5h, best discovery rate
12. **Batch API for costs** - 6-8h, 50% cheaper
13. **Confidence scores** - 6-8h, better quality assessment
14. **Experiment tracking** - 4-6h, data-driven improvements
15. **URL pattern analysis** - 6-8h, understand what works

### Lower ROI (Future)
16. ML-based filtering - 8-10h, auto-adapting quality
17. Breadcrumb discovery - 5-6h, alternative method
18. Multiple API keys - 2-3h, faster runs
19. Advanced monitoring - 15+h, production-ready
20. ML URL prediction - 20+h, long-term investment

---

## Implementation Priorities

### Phase 1: Fix US Performance (Week 1)
- Lower threshold to 5
- Add top 20 US URL patterns
- Implement sitemap.xml discovery
- Cache all GPT responses

**Expected:** US performance improves from 0.25 to 1.5+ contacts/uni

### Phase 2: Quality & Reliability (Week 2)
- Few-shot prompts
- JSON mode for GPT
- Retry logic with backoff
- MX record validation
- Duplicate detection

**Expected:** Higher quality contacts, fewer failures

### Phase 3: Cost & Speed (Week 3)
- Batch API integration
- Progress monitoring
- Better caching strategy
- Multiple API keys (optional)

**Expected:** 50% cost reduction, faster runs, better visibility

### Phase 4: Intelligence (Future)
- ML-based filtering
- Search API integration
- Experiment framework
- Advanced analytics

**Expected:** Self-improving system, data-driven optimization

---

## Success Metrics

Track these to measure improvement:

1. **Discovery Rate:** % of universities where candidates found
   - Target: >65% (currently 34% for US)

2. **Filtering Pass Rate:** % of extracted contacts kept
   - Target: >8% (currently 3.5% for US)

3. **Cost per Contact:** Average $ spent per kept contact
   - Target: <$2 (track and optimize)

4. **Success Rate:** % of universities yielding >1 contact
   - Target: >50% (currently ~20% for US)

5. **Cache Hit Rate:** % of requests served from cache
   - Target: >70% on reruns

6. **Average Confidence:** Quality score of kept contacts
   - Target: >75/100

---

## Notes

- Focus on quick wins first - sitemap.xml and threshold change
- US vs GB gap is fixable with targeted improvements
- Cache everything - GPT is expensive
- Measure before and after every change
- Don't over-engineer - start simple, iterate

---

## Files to Create/Modify

**New Files:**
- `gc_contacts/cache.py` - Caching layer
- `gc_contacts/retry.py` - Retry logic
- `gc_contacts/sitemap.py` - Sitemap discovery
- `gc_contacts/validation.py` - Email/contact validation
- `gc_contacts/monitoring.py` - Webhooks and alerts
- `gc_contacts/experiments.py` - A/B testing framework
- `gc_contacts/ml_filter.py` - ML-based filtering (future)

**Modify:**
- `gc_contacts/config.py` - Add US patterns, improve rate limiting
- `gc_contacts/filtering.py` - Lower threshold, add validations
- `gc_contacts/discovery.py` - Add sitemap method
- `gc_contacts/extraction.py` - Improve prompts, add JSON mode
- `benchmark_runner.py` - Add monitoring, experiment tracking
- `view_benchmark_contacts.html` - Add quality metrics
