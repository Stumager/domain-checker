"""Scripts directory README"""

# Utility Scripts

This directory contains standalone utility scripts for domain management and analysis.

## Available Scripts

### wayback_snapshots.py
Search Wayback Machine snapshots for a domain.

**Usage:**
```bash
python wayback_snapshots.py
# Then enter domain name when prompted
```

**Features:**
- List all HTML snapshots
- Show HTTP status codes
- Display redirect targets for 30x responses
- Reverse chronological order

---

### check_redirects.py
Check HTTP status codes and redirect chains.

**Usage:**
```bash
python check_redirects.py
# Then enter domain/URL when prompted
```

**Features:**
- Check HTTP status code
- Follow redirect chains
- Show final destination
- Timeout protection

---

### find_duplicates.py
Find and remove duplicate domains within a file.

**Usage:**
```bash
python find_duplicates.py domains.txt [output.txt]
```

**Features:**
- Count total and unique domains
- Report duplication statistics
- Save unique list to new file
- Case-insensitive matching

**Example:**
```bash
python find_duplicates.py available.txt unique_available.txt
```

---

### find_inter_duplicates.py
Find duplicate domains across multiple files.

**Usage:**
```bash
python find_inter_duplicates.py file1.txt file2.txt file3.txt
```

**Features:**
- Compare multiple result files
- Identify overlapping domains
- Show which files contain each domain
- Useful for comparing available/taken/errors results

**Example:**
```bash
python find_inter_duplicates.py available.txt taken.txt errors.txt
```

---

### aggregate_csv.py
Aggregate domain data from multiple CSV files.

**Usage:**
```bash
python aggregate_csv.py input1.csv [input2.csv ...] [output.csv]
```

**Features:**
- Merge CSV domain lists
- Preserve status information
- Deduplicate across files
- Export to single CSV

**CSV Format Expected:**
```csv
domain,status
example.com,available
test.es,taken
```

---

## Common Use Cases

### Clean and Deduplicate Results
```bash
python find_duplicates.py checker_results.txt clean_results.txt
```

### Compare Multiple Checks
```bash
python find_inter_duplicates.py batch1/available.txt batch2/available.txt
```

### Aggregate Multiple Checks
```bash
python aggregate_csv.py results1.csv results2.csv combined.csv
```

### Browse History
```bash
python wayback_snapshots.py
# Enter: example.com
```

---

## Requirements

All scripts use only Python standard library except:
- `requests` library (for Wayback Machine API and redirects)

Install if needed:
```bash
pip install requests
```

## Output

Scripts save results to files or display in console:
- **Text output** → Console and optionally to .txt file
- **CSV output** → Properly formatted .csv file
- **URLs** → Wayback Machine links for visit
