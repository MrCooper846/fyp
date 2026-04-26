# Benchmark Runs Organization

The benchmark script now automatically organizes results into timestamped folders with metadata.

## New Structure

```
benchmark_runs/
├── GB_20unis_20251209_165315/          ← Each run in its own folder
│   ├── run_info.json                   ← Run metadata (country, count, methods, etc)
│   ├── benchmark_results.csv           ← Statistics
│   └── contacts/                       ← Contact JSON files
│       ├── heuristic_university_*.json
│       ├── ai_slug_university_*.json
│       └── ai_crawler_university_*.json
├── US_50unis_20251209_120000/
│   ├── run_info.json
│   ├── benchmark_results.csv
│   └── contacts/
└── CN_10unis_20251208_094532/
    ├── run_info.json
    ├── benchmark_results.csv
    └── contacts/
```

## Usage

```bash
# Run benchmark - automatically creates organized folder
python benchmark_runner.py --country GB --limit 20

# Run with custom output directory
python benchmark_runner.py --country GB --limit 20 --output-dir my_benchmarks
```

## What Gets Saved

### `run_info.json` - Run Metadata
```json
{
  "timestamp": "2025-12-09T16:53:16.488236",
  "country": "GB",
  "num_universities": 20,
  "methods": ["heuristic", "ai_slug", "ai_crawler"],
  "probe_max": 10,
  "run_name": "GB_20unis_20251209_165315"
}
```

### `benchmark_results.csv` - Statistics
```csv
method,university,candidates_found,contacts_extracted,contacts_kept,time_seconds,cost_dollars
heuristic,University of Oxford,24,8,5,3.2,0.0000
ai_slug,University of Oxford,18,12,8,5.1,0.0245
ai_crawler,University of Oxford,15,14,11,4.8,0.0156
```

### `contacts/` Folder - Detailed Contact Data
Each method gets its own JSON files with actual contact objects.

## Folder Naming

Run folders are named: `{COUNTRY}_{NUM_UNIS}unis_{TIMESTAMP}`

Examples:
- `GB_20unis_20251209_165315` - 20 UK universities, Dec 9 2025, 16:53:15
- `US_50unis_20251210_090000` - 50 US universities, Dec 10 2025, 09:00:00
- `CN_10unis_20251209_120000` - 10 Chinese universities, Dec 9 2025, 12:00:00

## Benefits

✅ **Organized**: Each run has its own folder  
✅ **Trackable**: Timestamp shows when run occurred  
✅ **Documented**: run_info.json has all parameters  
✅ **Comparable**: Easy to compare multiple runs  
✅ **Archiveable**: Keep old runs for historical comparison  

## Example Workflow

```bash
# Run 1: Test on 5 UK universities
python benchmark_runner.py --country GB --limit 5

# Creates: benchmark_runs/GB_5unis_20251209_150000/

# Run 2: Test on 20 UK universities  
python benchmark_runner.py --country GB --limit 20

# Creates: benchmark_runs/GB_20unis_20251209_160000/

# Run 3: Test on US
python benchmark_runner.py --country US --limit 20

# Creates: benchmark_runs/US_20unis_20251209_170000/

# Results:
# benchmark_runs/
# ├── GB_5unis_20251209_150000/
# ├── GB_20unis_20251209_160000/    ← Can compare these two
# └── US_20unis_20251209_170000/
```

## Accessing Run Data

### View run metadata
```bash
cat benchmark_runs/GB_20unis_20251209_165315/run_info.json
```

### View statistics
```bash
cat benchmark_runs/GB_20unis_20251209_165315/benchmark_results.csv
```

### View contact data  
```bash
# List all contact files
ls benchmark_runs/GB_20unis_20251209_165315/contacts/

# View specific method
cat benchmark_runs/GB_20unis_20251209_165315/contacts/heuristic_*.json | jq .contacts
```

### Use with dashboard
Copy the `contacts/` folder to `benchmark_contacts/` to view in dashboard:
```bash
cp -r benchmark_runs/GB_20unis_20251209_165315/contacts/* benchmark_contacts/
python serve_dashboard.py
```

## Command Reference

```bash
# Basic run (20 universities from GB)
python benchmark_runner.py --country GB --limit 20

# All options
python benchmark_runner.py \
  --country GB \
  --limit 20 \
  --methods heuristic ai_slug ai_crawler \
  --probe-max 10 \
  --output-dir benchmark_runs \
  --ignore-robots

# Run without waiting (redirect to file)
python benchmark_runner.py --country GB --limit 20 > run.log 2>&1 &
```

## Notes

- Default output directory: `benchmark_runs/`
- Each run is isolated in its own folder
- Multiple runs can be compared side-by-side
- Metadata helps you remember what each run tested
- Old runs are never deleted automatically
