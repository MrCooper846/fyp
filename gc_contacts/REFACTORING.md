# gc_contacts Refactored Architecture

## Overview

The original monolithic `gc_contacts_v2_3_fix.py` has been refactored into a modular package structure for better maintainability, testability, and iterative improvement of individual components.

## Module Structure

### Core Modules

**`gc_contacts/`** - Main package directory

#### `config.py`
- Global configuration, constants, and state
- API keys, headers, timeout settings
- Token bucket for rate limiting
- Global HTTP client and OpenAI client
- Debug flags and directories
- Regex patterns and keyword lists

#### `models.py`
- Data classes and type definitions
- `Contact` - Extracted contact information
- `Candidate` - Candidate pages to probe
- `University` - University to process
- `ProcessResult` - Result of processing
- `URLFeatures` - Extracted URL features

#### `http_client.py`
- HTTP fetching with caching and retries
- URL normalization
- Cache read/write operations
- HTML text extraction (BeautifulSoup)
- Robots.txt compliance checking
- Per-host rate limiting

#### `discovery.py`
- Candidate page discovery from multiple sources
- Navigation/footer link extraction
- Sitemap parsing
- Subdomain probing
- hreflang handling
- WordPress API search
- Drupal JSON API search
- CMS detection
- Heuristic URL scoring

#### `extraction.py`
- Email extraction from page content
- JavaScript obfuscation decoding
- Email deobfuscation (e.g., `name[at]domain[dot]com`)
- Regex-based contact extraction with context snippets
- Generic email filtering (info@, admissions@, etc.)

#### `llm.py`
- GPT-powered slug suggestions
- LLM contact extraction from page text
- Name validation and cleaning via GPT
- Token bucket rate limiting for GPT calls

#### `filtering.py`
- Contact validation logic
- Role scoring (international relevance)
- Domain validation
- Name validation heuristics
- Generic email detection

#### `openalex.py`
- Fetching institutions from OpenAlex API
- Country-based filtering with cursor pagination

#### `debug.py`
- Writing per-university debug JSON
- Appending rows to training CSV
- Safe filename slug generation

#### `utils.py`
- Utility functions
- Token estimation
- URL feature extraction
- Safe slug generation
- Domain extraction

#### `main.py`
- Main orchestration and crawling logic
- Per-university processing (`process_uni`)
- Contact extraction from candidate pages
- Pagination handling
- Top-K ranking per university
- Output to CSV/XLSX

#### `__init__.py`
- Package initialization
- Version info

### Entry Points

**`gc_contacts_cli.py`**
- CLI entry point
- Replaces the old `gc_contacts_v2_3_fix.py`
- Same command-line interface

**`app.py`** (Updated)
- Flask web interface
- Now imports from `gc_contacts.main` instead of the monolithic script

## Why This Refactoring?

### Benefits

1. **Isolated Testing** - Each module can be tested independently
   - Test discovery strategies without hitting the web
   - Test filtering logic with synthetic data
   - Test LLM logic with mocked responses

2. **Iterative Improvement** - Easy to enhance individual components
   - Improve discovery heuristics in `discovery.py`
   - Refine filtering rules in `filtering.py`
   - Experiment with different extraction methods in `extraction.py`

3. **Code Reusability** - Import and use specific components
   ```python
   from gc_contacts.discovery import score_candidate
   from gc_contacts.filtering import keep_contact
   from gc_contacts.extraction import simple_regex_contacts
   ```

4. **Maintainability** - Clear separation of concerns
   - Each module has a single responsibility
   - Easier to find and fix bugs
   - Easier to onboard new developers

5. **Research & Experimentation** - Create variants without touching core
   ```python
   # Try new discovery method
   from gc_contacts import discovery
   
   async def discover_via_google():
       # New implementation
       pass
   
   discovery.discover_candidates = discover_via_google  # Override
   ```

## Usage

### Command Line

```bash
# Same as before, now using the modular structure
python gc_contacts_cli.py GB --limit 10 --debug --outfile results.csv
```

### Programmatic (e.g., Flask app)

```python
from gc_contacts.main import run_all
import asyncio

asyncio.run(run_all(
    country="GB",
    limit=10,
    outfile="results.csv",
    emit_all=True,
    debug=True,
    debug_dir="debug_logs",
    ignore_robots=False,
    verbose=False,
    browser_ua=True,
    per_uni_max=12,
    verify_names=False
))
```

### Using Specific Modules

```python
from gc_contacts.discovery import gather_candidates, score_candidate
from gc_contacts.filtering import keep_contact
from gc_contacts.extraction import simple_regex_contacts
from gc_contacts.http_client import fetch_page, bs_text

# Use individual components in your research
candidates = await gather_candidates("https://example.edu")
# ... iterate and improve ...
```

## Testing Individual Components

Create a test file `test_discovery.py`:

```python
import pytest
from gc_contacts.discovery import score_candidate

def test_score_candidate():
    # High score for good candidates
    assert score_candidate("/international/contact") > 3.0
    assert score_candidate("/people/directory") > 3.0
    
    # Low score for irrelevant URLs
    assert score_candidate("/news/article") < 1.0
    assert score_candidate("/student-loans") < 1.0
```

## Configuration Customization

All global configuration is in `gc_contacts/config.py`. You can:

1. **Adjust crawling limits:**
   ```python
   import gc_contacts.config as config
   config.PROBE_LIMIT = 50  # Probe more pages per uni
   config.CONCURRENCY = 20  # More parallel processing
   ```

2. **Change scoring heuristics:**
   ```python
   config.TOKENS.append("liaison")  # Add new keyword
   ```

3. **Modify extraction patterns:**
   ```python
   import gc_contacts.extraction as ext
   # Add new pattern or override
   ```

## Migration from Old Script

If you have code referencing `gc_contacts_v2_3_fix.run_all()`:

**Old:**
```python
from gc_contacts_v2_3_fix import run_all
```

**New:**
```python
from gc_contacts.main import run_all
```

The function signature remains the same, so it's a drop-in replacement.

## Future Improvements

With this structure, it's now easy to:

- Create alternative discovery strategies (e.g., `discovery_v2.py`)
- Implement A/B testing between extraction methods
- Add new data sources (LinkedIn, GitHub, etc.)
- Create specialized filters for different institution types
- Build a web UI for parameter tuning
- Generate research reports from debug data
