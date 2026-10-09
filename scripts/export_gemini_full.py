#!/usr/bin/env python3
"""
Export FULL BBC corpus by half-year periods (900+ articles per half-year)
for Gemini web interface analysis.
"""
import json
import subprocess
from pathlib import Path
from collections import Counter

# Output directory
OUTPUT_DIR = Path("data/gemini_export_full")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def load_bbc_data():
    """Load BBC articles from git."""
    result = subprocess.run(
        ['git', 'show', 'FETCH_HEAD:data/BBC/filtered.json'],
        capture_output=True
    )
    content = result.stdout.decode('utf-8', errors='ignore')
    return json.loads(content)

def get_period(date_str):
    """Convert YYYY-MM-DD to YYYY-H1/H2."""
    if not date_str or len(date_str) < 7:
        return None
    year = int(date_str[:4])
    month = int(date_str[5:7])
    half = "H1" if month <= 6 else "H2"
    return f"{year}-H{1 if month <= 6 else 2}"

def export_by_period(data):
    """Group articles by period and export each to separate file."""
    WAR_START = "2022-02-24"
    
    periods = {}
    for a in data:
        date_str = a.get('date', '')[:10]
        if not date_str or date_str < WAR_START:
            continue
        period = get_period(date_str)
        if period:
            periods.setdefault(period, []).append(a)
    
    for period, articles in sorted(periods.items()):
        output_file = OUTPUT_DIR / f"{period}.txt"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"=== PERIOD: {period} ===\n")
            f.write(f"Total articles: {len(articles)}\n")
            f.write("=" * 80 + "\n\n")
            
            for i, article in enumerate(articles, 1):
                f.write(f"--- Article {i} ---\n")
                f.write(f"Date: {article.get('date', 'N/A')}\n")
                f.write(f"Title: {article.get('title', 'N/A')}\n")
                f.write("\n")
        
        print(f"Exported {len(articles)} articles to {output_file}")

def main():
    print("Loading BBC data from git...")
    data = load_bbc_data()
    print(f"Total BBC articles: {len(data)}")
    
    print("\nExporting by half-year period (from 2022-02-24)...")
    export_by_period(data)
    
    print(f"\nDone! Files saved to {OUTPUT_DIR}")

if __name__ == "__main__":
    main()