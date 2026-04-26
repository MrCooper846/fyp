# 🌐 Benchmark Contacts Dashboard

Interactive web-based viewer for your benchmark contact data.

## Features

✨ **Visual Overview**
- Total contacts across all methods
- Average contacts per university
- Best performing method
- Total cost breakdown

🔍 **Smart Filtering**
- Filter by discovery method (heuristic, ai_slug, ai_crawler)
- Filter by university
- Search by name, email, or role
- Sort by university, contact count, time, or cost

📊 **Rich Display**
- Color-coded method tabs
- Detailed contact cards with roles and emails
- Source URL for each contact
- Performance metrics per university

## Quick Start

### Option 1: Python Server (Recommended)
```bash
python serve_dashboard.py
```

Opens automatically at `http://localhost:8000/view_benchmark_contacts.html`

### Option 2: Manual Server
```bash
# Python 3
python -m http.server 8000

# Then open: http://localhost:8000/view_benchmark_contacts.html
```

### Option 3: Manual File Loading
If you can't run a server, open `view_benchmark_contacts.html` directly and use the file upload feature to load JSON files manually.

## Usage

1. **View All Methods**: Default view shows contacts from all discovery methods
2. **Filter by Method**: Click tabs (Heuristic, AI Slug, AI Crawler) to see specific method
3. **Search**: Type in search box to find specific names, emails, or roles
4. **Filter University**: Dropdown to view specific university only
5. **Sort**: Change sort order (by university, contact count, time, cost)

## Dashboard Layout

```
┌─────────────────────────────────────────────────────────┐
│  Header: Benchmark Contacts Viewer                      │
├─────────────────────────────────────────────────────────┤
│  Stats: Total | Avg/Uni | Best Method | Total Cost      │
├─────────────────────────────────────────────────────────┤
│  Filters: Search | University | Sort                    │
├─────────────────────────────────────────────────────────┤
│  Tabs: All | Heuristic | AI Slug | AI Crawler           │
├─────────────────────────────────────────────────────────┤
│  University Cards:                                       │
│  ┌───────────────────────────────────────────────────┐  │
│  │ University Name                                   │  │
│  │ Method | Contacts | Candidates | Time | Cost     │  │
│  │ ─────────────────────────────────────────────────│  │
│  │ Contact 1: Name, Role, Email, Source             │  │
│  │ Contact 2: Name, Role, Email, Source             │  │
│  │ ...                                               │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Example Workflow

### Compare Methods Side-by-Side
```
1. Start with "All Methods" tab
2. Look at stats to see which method found most contacts
3. Click specific method tab (e.g., "AI Crawler")
4. Sort by "Contact Count" to see which universities worked best
5. Inspect actual contact quality (names, roles, emails)
```

### Find Specific Contacts
```
1. Type name/email in search box
2. Results filter in real-time
3. See which method found that contact
4. View source URL where they were discovered
```

### Analyze Specific University
```
1. Select university from dropdown
2. See all methods that ran on that university
3. Compare contact count across methods
4. Inspect quality of contacts found
```

## Color Scheme

- **Purple Gradient**: Headers and active elements
- **Blue (#667eea)**: Contact highlights and borders
- **Gray Scale**: Text hierarchy and metadata
- **White**: Cards and backgrounds

## Files

- `view_benchmark_contacts.html` - Main dashboard (standalone HTML file)
- `serve_dashboard.py` - Python web server (optional, for convenience)
- `benchmark_contacts/*.json` - Data source files

## Technical Details

**Frontend Only**: Pure HTML/CSS/JavaScript, no build process needed

**Data Loading**:
- Automatically loads all `.json` files from `benchmark_contacts/` folder
- Falls back to manual file upload if server not available
- Client-side filtering and sorting (fast, no server processing)

**Browser Support**: Modern browsers (Chrome, Firefox, Safari, Edge)

## Troubleshooting

### "Error loading data"
**Cause**: Browser security prevents loading files without a web server

**Fix**: Use `python serve_dashboard.py` or `python -m http.server 8000`

### "No data matches your filters"
**Cause**: Filters are too restrictive

**Fix**: Reset filters (select "All Universities", clear search, click "All Methods")

### Colors/Layout broken
**Cause**: Old browser

**Fix**: Update to modern browser (Chrome 90+, Firefox 88+, Safari 14+)

## Example Screenshots (What You'll See)

### Stats Overview
```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ Total Contacts│ Avg per Uni │ Best Method  │ Total Cost   │
│      156      │     7.8      │  ai_crawler  │   $25.07     │
└──────────────┴──────────────┴──────────────┴──────────────┘
```

### Contact Card
```
┌───────────────────────────────────────────────────────────┐
│ King's College London                                     │
│ http://www.kcl.ac.uk/index.aspx                          │
├───────────────────────────────────────────────────────────┤
│ Method: ai_crawler | Contacts: 14 | Time: 13.2s | $25.07│
├───────────────────────────────────────────────────────────┤
│ ┌───────────────────────────────────────────────────────┐ │
│ │ Renato Nazzini KC (Hon)                              │ │
│ │ Director of the Centre of Construction Law           │ │
│ │ nazzini@kcl.ac.uk                                     │ │
│ │ Source: https://www.kcl.ac.uk/law/...                │ │
│ └───────────────────────────────────────────────────────┘ │
│ [more contacts...]                                        │
└───────────────────────────────────────────────────────────┘
```

## Next Steps

After using the dashboard:
1. Identify which method found best quality contacts
2. Check if contacts are real people with valid roles
3. Compare cost-to-quality ratio
4. Make decision: which method to use for production?

---

**Ready to use!** Run `python serve_dashboard.py` and explore your benchmark results visually. 🚀
