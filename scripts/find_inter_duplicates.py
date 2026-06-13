"""Utility script for batch inter-domain duplicate detection"""

import sys
from pathlib import Path
from collections import defaultdict


def find_inter_duplicates(file_paths):
    """
    Find duplicates that appear across multiple files
    
    Args:
        file_paths: List of input file paths
    """
    domain_files = defaultdict(list)
    
    for file_path in file_paths:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                domains = f.readlines()

            seen_in_file = set()
            for domain in domains:
                domain = domain.strip().lower()
                if domain and domain not in seen_in_file:
                    seen_in_file.add(domain)
                    domain_files[domain].append(file_path)
        
        except FileNotFoundError:
            print(f"Warning: File not found - {file_path}")
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    
    # Find duplicates across files
    inter_duplicates = {d: files for d, files in domain_files.items() if len(files) > 1}
    
    print(f"Total unique domains: {len(domain_files)}")
    print(f"Duplicated across files: {len(inter_duplicates)}\n")
    
    if inter_duplicates:
        print("Duplicates found in multiple files:")
        for domain, files in sorted(inter_duplicates.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {domain}: {', '.join(files)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_inter_duplicates.py <file1> <file2> [file3] ...")
        print("Example: python find_inter_duplicates.py available.txt taken.txt errors.txt")
        sys.exit(1)
    
    find_inter_duplicates(sys.argv[1:])
