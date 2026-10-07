import json
import os
from pathlib import Path
from scrapers.sites.bbc import BBC_Scraper, BBC_ArticleParser, BBC_ArticleFilter

BASE_DIR = Path(__file__).resolve().parent.parent
BBC_DIR = f"{BASE_DIR}/data/BBC"
os.makedirs(BBC_DIR, exist_ok=True)

# URL scrapping
bbc_scraper = BBC_Scraper()
all_bbc_articles = bbc_scraper.scrape()

urls_save_path = os.path.join(BBC_DIR, "urls.json")
bbc_scraper.save_urls(all_bbc_articles, urls_save_path)

# Article Parsing
bbc_parser = BBC_ArticleParser(source_name='bbc', target_years=range(2021, 2027))
with open(urls_save_path, 'r') as file:
    urls_dict = json.load(file)

articles_21_26 = dict()

# 2021 рік ~ 90-тий архів
for i, sitemap_url in enumerate(urls_dict.keys(), start=1):
    if i >= 90:
        articles_21_26[sitemap_url] = urls_dict[sitemap_url]

all_urls = []
for sitemap, urls in articles_21_26.items():
    if urls:
        all_urls.extend(urls)

bbc_parser.scrape_corpus(all_urls, BBC_DIR)

# Article filter
bbc_filter = BBC_ArticleFilter()
filtered_save_path = os.path.join(BBC_DIR, "filtered.json")
filtered_articles = bbc_filter.filter(
    articles_file_path=f'{os.path.join(BBC_DIR, "bbc_articles.json")}',
    save_path=filtered_save_path)
