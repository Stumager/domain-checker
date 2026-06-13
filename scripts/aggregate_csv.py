"""Utility script for aggregating CSV domain data"""

import csv
import sys
from pathlib import Path
from collections import defaultdict


def aggregate_csv(input_files, output_file=None):
    """
    Aggregate domains from multiple CSV files
    
    Args:
        input_files: List of CSV file paths
        output_file: Output file path (optional)
    """
    domains = defaultdict(set)
    
    for file_path in input_files:
        try:
            with open(file_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Try common column names
                    domain = row.get('domain') or row.get('Domain') or row.get('name') or row.get('Name')
                    status = row.get('status') or row.get('Status') or 'unknown'
                    
                    if domain:
                        domain = domain.strip().lower()
                        domains[status].add(domain)
        
        except FileNotFoundError:
            print(f"Warning: File not found - {file_path}")
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    
    # Print summary
    total = sum(len(d) for d in domains.values())
    print(f"Total domains aggregated: {total}\n")
    
    for status, domain_set in sorted(domains.items()):
        print(f"{status}: {len(domain_set)} domains")
    
    # Save to file if requested
    if output_file:
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['domain', 'status'])
            
            for status, domain_set in domains.items():
                for domain in sorted(domain_set):
                    writer.writerow([domain, status])
        
        print(f"\nAggregated data saved to: {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python aggregate_csv.py <file1.csv> [file2.csv ...] [output.csv]")
        print("Example: python aggregate_csv.py data1.csv data2.csv aggregated.csv")
        sys.exit(1)

    args = sys.argv[1:]
    output_file = None
    input_files = list(args)

    if len(args) >= 2 and args[-1].lower().endswith(".csv"):
        output_file = args[-1]
        input_files = args[:-1]

    if not input_files:
        print("Error: at least one input CSV file is required")
        sys.exit(1)

    aggregate_csv(input_files, output_file)
