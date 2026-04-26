# Benchmark Contact Export - Implementation Summary

## Problem
The benchmark framework was collecting and saving statistics (contacts_kept, time, cost) but **discarding actual Contact objects**. This made it impossible to inspect the quality of discovered contacts or compare what each method actually found.

## Solution
Enhanced the benchmark to capture and export **actual Contact objects** alongside statistics.

## Changes Made

### 1. `gc_contacts/benchmark.py`

#### Added Contact Storage to `DiscoveryResult`
```python
@dataclass
class DiscoveryResult:
    # ... existing fields ...
    contacts_list: List[Dict] = field(default_factory=list)  # NEW
```

#### Updated `probe_candidates_and_extract()` Return Type
**Before:**
```python
def probe_candidates_and_extract(...) -> Tuple[int, int, str, int, int]:
    return len(all_contacts), kept_count, best_url or "", tokens_in, tokens_out
```

**After:**
```python
def probe_candidates_and_extract(...) -> Tuple[int, int, str, int, int, List[Dict]]:
    return len(all_contacts), kept_count, best_url or "", tokens_in, tokens_out, kept_contacts
```

Now captures and returns the actual Contact objects that passed filtering.

#### Updated `benchmark_methods()` to Store Contacts
```python
# OLD: Only stored counts
result = DiscoveryResult(
    method=method,
    university_name=uni.get("name", "Unknown"),
    # ... other fields ...
)

# NEW: Also stores actual contacts
result = DiscoveryResult(
    method=method,
    university_name=uni.get("name", "Unknown"),
    # ... other fields ...
    contacts_list=kept_contacts,  # NEW
)
```

#### Added `export_contacts()` Method to `ComparisonReport`
```python
def export_contacts(self, output_dir: Path):
    """Export actual contacts to JSON files per method per university."""
    # Creates files like:
    # heuristic_university_of_oxford_contacts.json
    # ai_slug_university_of_oxford_contacts.json
    # ai_crawler_university_of_oxford_contacts.json
```

Each JSON file contains:
```json
{
  "method": "heuristic",
  "university": "University of Oxford",
  "university_url": "http://ox.ac.uk",
  "timestamp": "2025-01-14T10:30:00",
  "summary": {
    "total_kept": 5,
    "candidates_found": 24,
    "candidates_probed": 10,
    "contacts_extracted": 8,
    "time_seconds": 3.2,
    "cost_dollars": 0.0001
  },
  "contacts": [
    {
      "name": "Dr. Jane Smith",
      "email": "j.smith@oxford.ac.uk",
      "role": "Department Head",
      "page_url": "https://www.oxford.ac.uk/about/staff/"
    },
    // ... more contacts ...
  ]
}
```

### 2. `benchmark_runner.py`

#### Added `--contacts-dir` Parameter
```python
parser.add_argument(
    "--contacts-dir",
    default="benchmark_contacts",
    help="Directory to save contact JSON files"
)
```

#### Added Contact Export Call
```python
# After saving statistics CSV, also export contacts
contacts_dir = Path(args.contacts_dir)
report.export_contacts(contacts_dir)
LOG.info(f"Contact details saved to {contacts_dir}")
```

### 3. New Files Created

#### `analyze_contacts.py`
Script to analyze and display contact data from benchmark runs:
```bash
python analyze_contacts.py --dir benchmark_contacts
python analyze_contacts.py --dir benchmark_contacts --method heuristic
```

Outputs summary of contacts per method and shows sample contact details.

#### `test_benchmark_contacts.py`
Test suite verifying the contact export functionality:
- `test_discovery_result_with_contacts()` - Verify DiscoveryResult stores contacts
- `test_comparison_report_export()` - Verify JSON export works
- `test_probe_return_signature()` - Verify function signatures updated

✅ All tests passing

#### `BENCHMARK_CONTACTS_EXPORT.md`
Documentation explaining how to use the contact export feature.

## Usage

### Run Benchmark with Contact Export
```bash
python benchmark_runner.py \
  --country GB \
  --limit 10 \
  --methods heuristic ai_slug ai_crawler \
  --output benchmark_results.csv \
  --contacts-dir benchmark_contacts
```

**Outputs:**
- `benchmark_results.csv` - Statistics for each method per university
- `benchmark_contacts/heuristic_*.json` - Actual contacts found by heuristic method
- `benchmark_contacts/ai_slug_*.json` - Actual contacts found by AI slug method
- `benchmark_contacts/ai_crawler_*.json` - Actual contacts found by AI crawler method

### Analyze Contact Data
```bash
# Show summary statistics
python analyze_contacts.py --dir benchmark_contacts

# Show details for specific method
python analyze_contacts.py --dir benchmark_contacts --method heuristic
```

## What You Can Now Do

1. **Inspect Contact Quality**
   - See actual names, emails, roles discovered by each method
   - Identify false positives or generic contacts

2. **Compare Discovery Methods**
   - Which method finds more relevant contacts?
   - Which method has better precision (fewer false positives)?
   - Which method has best cost-to-quality ratio?

3. **Analyze Patterns**
   - What types of roles does each method find?
   - Which URLs are most effective for finding contacts?
   - How many contacts per method per university on average?

4. **Make Informed Decision**
   - Based on actual contact data, decide which method to use
   - Potential decision factors:
     - **Heuristic**: Fastest, no cost, but limited coverage
     - **AI Slug**: Moderate time/cost, better coverage than heuristic
     - **AI Crawler**: Slowest/most expensive, but potentially highest quality

## Example Workflow

```bash
# 1. Run benchmark on 20 GB universities
python benchmark_runner.py --country GB --limit 20 --contacts-dir benchmark_contacts

# 2. Check aggregate statistics
cat benchmark_results.csv | column -t -s,

# 3. Analyze contacts
python analyze_contacts.py --dir benchmark_contacts

# 4. Deep dive into specific method
python analyze_contacts.py --dir benchmark_contacts --method ai_crawler

# 5. Manually review JSON files
cat benchmark_contacts/heuristic_university_of_oxford_contacts.json | jq '.contacts[0:3]'
```

## Test Results
✅ All functionality tested and working:
- DiscoveryResult stores contacts correctly
- ComparisonReport exports JSON files properly
- Function signatures updated correctly
- Contact objects serialized correctly to JSON

## Next Steps for User

1. **Run benchmark**: Execute with `--country GB --limit 10-20` to get statistical significance
2. **Inspect results**: Use `analyze_contacts.py` to understand what each method finds
3. **Manual validation**: Pick 10-20 contacts per method and validate their quality
4. **Compare**: Determine which method has best:
   - Precision (relevant contacts / total contacts)
   - Recall (universities with ≥1 contact / total universities)
   - Cost-effectiveness (quality per dollar spent)
5. **Decision**: Select winner and integrate into main crawler

The benchmark now provides both **quantitative metrics** (statistics) and **qualitative insights** (actual contact data).
