from bs4 import BeautifulSoup
from datetime import datetime
import gzip
import json
from scrapers.base import BaseScraper, BaseParser, BaseFilter

class RadioScraper(BaseScraper):
    def __init__(self):
        super().__init__()
        self.ARCHIVE_URL = "https://www.radiosvoboda.org/sitemap.xml"

    def get_sitemap_urls(self):
        """
        Отримує список sitemap-ів
        """
        exclude = ('sections', 'videos')

        soup = BeautifulSoup(self.get_response(self.ARCHIVE_URL), "xml")

        sitemap_urls = [
            loc.get_text(strip=True)
            for loc in soup.find_all("loc")
        ]

        sitemap_urls_clean = [url
                              for url in sitemap_urls
                              if not url.strip('.xml.gz').endswith(exclude)]

        return sitemap_urls_clean

    def get_article_urls(self, sitemap_url):
        """
        Витягує URL новин із конкретного sitemap
        """
        # Розпаковуємо .gz
        content = gzip.decompress(self.get_response(sitemap_url))
        soup = BeautifulSoup(content, "xml")

        article_urls = [
            loc.get_text(strip=True)
            for loc in soup.find_all("loc")
        ]

        # Залишаємо тільки статті /a/
        article_urls = [
            url
            for url in article_urls
            if "/a/" in url
            and "/p/" not in url
        ]

        return article_urls

    def scrape(self):
        print("Getting sitemap list...") #

        sitemap_urls = self.get_sitemap_urls()
        print(f"Found {len(sitemap_urls)} sitemap files.")

        all_articles = dict()

        for i, sitemap_url in enumerate(sitemap_urls, start=1):

            print(f"\n[{i}/{len(sitemap_urls)}]")
            articles = self.get_article_urls(sitemap_url)

            print(f"Found {len(articles)} ")

            articles = list(set(articles)) # Видаляємо дублікати URL
            all_articles[sitemap_url] = articles

        return all_articles

class RadioArticleParser(BaseParser):
    def __init__(self, target_years=range(2021, 2027)) -> None:
       self.HEADERS = {"User-Agent": "Mozilla/5.0"}
       self.source_name = 'radio_svoboda'
       self.target_years = target_years
       self.stopwords_list = ["Посилання для територій, де сайт Радіо Свобода заблокований",
                              "Поділитись",
                              "Дивитись",
                              "No media source currently available",
                              "Підписатись",
                              "Друк",
                              "Працюю на Радіо Свобода",
                              "© Радіо Свобода",
                              "UTC",
                              ]

    def _convert_date(self, date_str):
      try:
        date = datetime.fromisoformat(
                date_str.replace("Z", "+00:00"))
        return date

      except ValueError:
        print('Date conversion fail:')
        print(date_str)
      return None

    def _get_article_date(self, soup):
        # <time>
        date_published = soup.find("time", attrs={"pubdate": "pubdate"})
        if date_published:
          date_str = date_published.get("datetime")
          return self._convert_date(date_str)

        # JSON-LD
        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        for script in scripts:
          if not script.string:
              continue
          try:
              data = json.loads(script.string)

          except json.JSONDecodeError:
              continue

          # Один JSON object
          if isinstance(data, dict):
              if "datePublished" in data:
                  return self._convert_date(data["datePublished"])

          # Список JSON objects
          elif isinstance(data, list):
              for item in data:
                if (
                    isinstance(item, dict)
                    and "datePublished" in item
                ):
                    return self._convert_date(item["datePublished"])

        return None

    def _get_article_title(self, soup):
        title = None
        meta_title = soup.find("meta", attrs={"property": "og:title"})
        if meta_title:
            title = meta_title.get("content")

        if not title:
          # JSON-LD
          scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
          for script in scripts:
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
              if "headline" in data or "page_title" in data:
                title = data.get("headline", "") if data.get("headline", "") else data.get("page_title")

        if not title:
          title_tag = soup.find("title")
          if title_tag:
            title = title_tag.get_text(" ", strip=True)

        if not title:
          h1 = soup.find("h1")
          if h1:
            title = h1.get_text(" ", strip=True)

        return title


    def _get_article_text(self, soup):
        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)
            if text and not any([text.startswith(el) for el in self.stopwords_list]):
                paragraphs.append(text)

        paragraphs = list(dict.fromkeys(paragraphs))
        article_text = "\n".join(paragraphs)
        return article_text


    def _get_tags(self, soup):
        tags = None
        meta_tags = soup.find("meta", attrs={"name": "keywords"})
        if meta_tags:
            tags_string = meta_tags.get("content")
            tags = [tag.strip() for tag in tags_string.split(',')]
        return tags

    def _get_description(self, soup):
      description_tag = (soup.find("meta", attrs={"name": "description"})
                      or soup.find("meta", attrs={"name": "twitter:description"}))
      description = (description_tag.get("content") if description_tag else None)
      return description
