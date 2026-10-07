#!/usr/bin/env python3
"""
Статистика корпусу: скільки статей з 24.02.2022 по сьогодні.
Виводить таблицю в термінал.
"""
import json
import subprocess
from datetime import datetime
from collections import Counter
from pathlib import Path

WAR_START = "2022-02-24"

def load_bbc():
    result = subprocess.run(
        ['git', 'show', 'FETCH_HEAD:data/BBC/filtered.json'],
        capture_output=True
    )
    content = result.stdout.decode('utf-8', errors='ignore')
    return json.loads(content)

def load_radiosvoboda():
    result = subprocess.run(
        ['git', 'show', 'FETCH_HEAD:data/RadioSvoboda/urls.json'],
        capture_output=True
    )
    content = result.stdout.decode('utf-8', errors='ignore')
    data = json.loads(content)
    # urls.json - це dict sitemap_url -> list of urls
    articles = []
    for sitemap, urls in data.items():
        for url in urls:
            articles.append({"url": url, "source": "radiosvoboda", "id": url})
    return articles

def print_stats(articles, source_name):
    if not articles:
        print(f"\n{source_name}: 0 статей")
        return
    
    dates = []
    for a in articles:
        d = a.get('date') or a.get('publish_date') or a.get('published_at')
        if d:
            dates.append(d[:10])
    
    if not dates:
        print(f"\n{source_name}: {len(articles)} статей (без дат)")
        return
    
    dates.sort()
    total = len(dates)
    war_start = WAR_START
    today = datetime.now().strftime("%Y-%m-%d")
    
    war_articles = [d for d in dates if d >= war_start]
    
    # По роках
    by_year = Counter(d[:4] for d in dates)
    war_by_year = Counter(d[:4] for d in war_articles)
    
    # По місяцях
    by_month = Counter(d[:7] for d in dates)
    war_by_month = Counter(d[:7] for d in war_articles)
    
    print(f"\n{'='*60}")
    print(f"{source_name.upper()}")
    print(f"{'='*60}")
    print(f"Загалом статей: {total}")
    print(f"Від {dates[0]} по {dates[-1]}")
    print(f"Від 24.02.2022 по {today}: {len(war_articles)} ({len(war_articles)/total*100:.1f}%)")
    
    print(f"\n{'Рік':<6} {'Усі':>6} {'Війна':>6} {'%':>6}")
    print("-"*28)
    for year in sorted(by_year.keys()):
        all_c = by_year[year]
        war_c = war_by_year.get(year, 0)
        pct = war_c/all_c*100 if all_c else 0
        marker = " *" if year >= "2022" else ""
        print(f"{year:<6} {all_c:>6} {war_c:>6} {pct:>5.1f}%{marker}")
    
    print(f"\n{'Місяць':<10} {'Усі':>6} {'Війна':>6} {'%':>6}")
    print("-"*32)
    for month in sorted(by_month.keys()):
        all_c = by_month[month]
        war_c = war_by_month.get(month, 0)
        pct = war_c/all_c*100 if all_c else 0
        marker = " *" if month >= "2022-02" else ""
        print(f"{month:<10} {all_c:>6} {war_c:>6} {pct:>5.1f}%{marker}")

if __name__ == "__main__":
    print("Підрахунок статистики корпусу...")
    
    bbc = load_bbc()
    print_stats(bbc, "BBC Ukrainian")
    
    rs = load_radiosvoboda()
    print_stats(rs, "Radio Svoboda (URLs only)")
    
    print(f"\n{'='*60}")
    print(f"ВОЄННИЙ ПЕРІОД (24.02.2022 - {datetime.now().strftime('%Y-%m-%d')})")
    print(f"BBC: {sum(1 for a in bbc if a.get('date','')[:10] >= WAR_START)} статей")
    print(f"RadioSvoboda: URLs only - потрібен скрапінг для дат")