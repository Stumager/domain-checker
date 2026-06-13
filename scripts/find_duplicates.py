"""Utility script for finding and deduplicating domains"""

import sys
from pathlib import Path


def find_duplicates(domain_list):
    """
    Find duplicate domains in a list
    
    Args:
        domain_list: List of domains
        
    Returns:
        Tuple of (duplicates dict, unique list)
    """
    seen = {}
    duplicates = {}
    unique = []
    
    for domain in domain_list:
        domain = domain.strip().lower()
        if not domain:
            continue
        
        if domain in seen:
            if domain not in duplicates:
                duplicates[domain] = 0
            duplicates[domain] += 1
        else:
            seen[domain] = True
            unique.append(domain)
    
    return duplicates, unique


def process_file(input_file, output_file=None):
    """
    Process file and remove duplicates
    
    Args:
        input_file: Input file path
        output_file: Output file path (optional)
    """
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            domains = f.readlines()
        
        duplicates, unique = find_duplicates(domains)
        
        print(f"Total lines: {len(domains)}")
        print(f"Unique domains: {len(unique)}")
        print(f"Duplicates found: {len(duplicates)}")
        
        if duplicates:
            print("\nDuplicate counts:")
            for domain, count in sorted(duplicates.items(), key=lambda x: x[1], reverse=True):
                print(f"  {domain}: {count} times")
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                for domain in unique:
                    f.write(domain + '\n')
            print(f"\nUnique domains saved to: {output_file}")
    
    except FileNotFoundError:
        print(f"Error: File not found - {input_file}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python find_duplicates.py <input_file> [output_file]")
        print("Example: python find_duplicates.py domains.txt unique_domains.txt")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    process_file(input_file, output_file)
