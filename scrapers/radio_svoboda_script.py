import os
from pathlib import Path
from scrapers.sites.radio_svoboda import RadioScraper

BASE_DIR = Path(__file__).resolve().parent.parent
RADIO_DIR = f"{BASE_DIR}/data/RadioSvoboda"
os.makedirs(RADIO_DIR, exist_ok=True)

# URL scrapping
radio_scraper = RadioScraper()
all_articles = radio_scraper.scrape()

urls_save_path = os.path.join(RADIO_DIR, "urls.json")
radio_scraper.save_urls(all_articles, urls_save_path)
