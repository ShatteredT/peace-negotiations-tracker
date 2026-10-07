import requests
import json
from bs4 import BeautifulSoup
import time
import os

class BaseScraper():
    def __init__(self) -> None:
        self.HEADERS = {"User-Agent": "Mozilla/5.0"}

    def get_response(self, url):
        """
        Завантажує вебсторінку за URL.
        """
        response = requests.get(url, headers=self.HEADERS, timeout=30)
        response.raise_for_status()
        return response.content

    def get_sitemap_urls(self):
        """
        Отримує URL усіх sitemap-ів з головного sitemap index
        """
        pass

    def get_article_urls(self, sitemap_url):
        """
        Витягує URL новин із конкретного sitemap
        """
        pass

    def scrape(self):
      pass

    def save_urls(self, all_articles, save_path):
        """
        Зберігає відфільтрований корпус url
        """

        filtered_articles = dict()

        for sitemap_url, articles_url_list in all_articles.items():
            if articles_url_list:
                filtered_articles[sitemap_url] = articles_url_list

        with open(save_path, "w", encoding="utf-8") as file:
            json.dump(filtered_articles, file, ensure_ascii=False, indent=2)


class BaseParser():
    def get_response(self, url):
        """
        Завантажує вебсторінку за URL. Повертає soup або None у разі помилки.
        """
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            return soup
        except requests.exceptions.RequestException as e:
            print(f"  ! Помилка завантаження {url}: {e}")
            return None

    def _get_article_date(self, soup):
        pass

    def _get_article_title(self, soup):
        pass

    def _get_article_text(self, soup):
        pass

    def _get_tags(self, soup):
        pass

    def _get_description(self, soup):
        pass

    def scrape_article(self, article_url):
        """
        Витягує дату, заголовок, опис, теги, текст зі статті
        """
        article_soup = self.get_response(article_url)
        if not article_soup:
            return None

        article_date = self._get_article_date(article_soup)
        if not article_date:
            return None

        if article_date.year in self.target_years:  # year filter

            article_title = self._get_article_title(article_soup)
            article_text = self._get_article_text(article_soup)
            article_tags = self._get_tags(article_soup)
            article_descr = self._get_description(article_soup)

            entry = {
                "title": article_title,
                "date": article_date.date().isoformat(),
                "description": article_descr,
                "tags": article_tags,
                "url": article_url,
                "text": article_text,
                "source": self.source_name
            }
            return entry
        return None

    def scrape_corpus(self, urls: list, save_dir):
        print(f"URLs to process: {len(urls)}")

        os.makedirs(save_dir, exist_ok=True)
        save_path = f"{save_dir}/{self.source_name}_articles.json"
        skipped_urls_path = f"{save_dir}/{self.source_name}_failed_urls.json"

        articles = []
        failed_urls = []
        total = len(urls)

        for i, url in enumerate(urls, start=1):
            print(f"[{i}/{total}] {url}")
            article = self.scrape_article(url)

            if article:
                articles.append(article)
                print(f"  + {article['date']} "
                      f"{article['title']}")
            else:
                failed_urls.append(url)
                print("  - skipped (помилка або фільтр за датою)")

            # Пауза між запитами
            time.sleep(0.5)

            # Проміжне збереження
            if i % 50 == 0:
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(articles, f, ensure_ascii=False, indent=2)
                if failed_urls:
                    with open(skipped_urls_path, "w", encoding="utf-8") as f:
                        json.dump(failed_urls, f, ensure_ascii=False, indent=2)
                print(f"\nSaved progress: {len(articles)} articles\n")

        # Фінальне збереження
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        if failed_urls:
            with open(skipped_urls_path, "w", encoding="utf-8") as f:
                json.dump(failed_urls, f, ensure_ascii=False, indent=2)

        print('Done')
        print(f"Articles: {len(articles)}")
        print(f"Failed/skipped: {len(failed_urls)}")


class BaseFilter():
    def filter(self, articles_file_path, save_path):
        pass
