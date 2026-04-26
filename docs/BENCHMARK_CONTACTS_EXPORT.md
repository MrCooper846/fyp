# Enhanced Benchmark: Contact Data Export

The benchmark framework now saves **actual Contact objects** so you can inspect and compare the quality of contacts discovered by each method.

## What Changed

### 1. `probe_candidates_and_extract()` Now Returns Contacts
Previously returned: `(contacts_extracted, contacts_kept, best_url, tokens_in, tokens_out)`  
Now returns: `(..., kept_contacts: List[Dict])`

The function now keeps track of contacts that pass filtering and returns the list.

### 2. `DiscoveryResult` Stores Contact Data
Added field: `contacts_list: List[Dict] = field(default_factory=list)`

Each result now contains the actual Contact objects found by that method on that university.

### 3. `ComparisonReport.export_contacts()` Method
New method that exports all contacts to JSON files:
```
benchmark_contacts/
├── heuristic_university_name_contacts.json
├── ai_slug_university_name_contacts.json
└── ai_crawler_university_name_contacts.json
```

Each file contains:
- Method name and university
- Summary statistics (total_kept, time, cost, etc.)
- Full list of Contact objects with fields: `name`, `email`, `role`, `page_url`, etc.

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

### Analyze Contact Data
```bash
# Summary of all contacts by method
python analyze_contacts.py --dir benchmark_contacts

# Show detailed contacts for specific method
python analyze_contacts.py --dir benchmark_contacts --method heuristic
```

## Contact Object Structure

Each contact in the JSON files has:
```json
{
  "name": "Dr. Jane Smith",
  "email": "j.smith@university.ac.uk",
  "role": "Department Head",
  "page_url": "https://www.university.ac.uk/contacts/",
  "extraction_method": "gpt"  // or "regex"
}
```

## What You Can Now Inspect

1. **Contact Quality**: See actual names, emails, and roles found by each method
2. **False Positives**: Inspect contacts that were filtered out (check filtering logic)
3. **Coverage**: Compare which methods found contacts on more universities
4. **Role Distribution**: See what types of roles each method extracts
5. **Source URLs**: Understand which pages each method finds contacts on

## Comparison Workflow

1. **Run benchmark**: `python benchmark_runner.py --country GB --limit 20`
2. **Check statistics**: `cat benchmark_results.csv`
3. **Inspect contacts**: `python analyze_contacts.py --dir benchmark_contacts --method heuristic`
4. **Compare quality**: Manually review 10-20 contacts from each method
5. **Determine winner**: Which method has best precision/relevance?

## Example Output Structure

```
benchmark_results.csv
├── method, university, candidates_found, candidates_probed, 
│   contacts_extracted, contacts_kept, time_seconds, cost_dollars
├── heuristic, University of Oxford, 24, 10, 8, 5, 3.2, 0.0001
├── ai_slug, University of Oxford, 18, 10, 12, 8, 5.1, 0.0245
└── ai_crawler, University of Oxford, 15, 10, 14, 11, 4.8, 0.0156

benchmark_contacts/
├── heuristic_university_of_oxford_contacts.json
│   └── Contains 5 Contact objects
├── ai_slug_university_of_oxford_contacts.json
│   └── Contains 8 Contact objects
└── ai_crawler_university_of_oxford_contacts.json
    └── Contains 11 Contact objects
```

You can now inspect the actual contacts from each method and make an informed decision about which discovery method is most effective.
