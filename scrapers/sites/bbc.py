from bs4 import BeautifulSoup
from datetime import datetime
import pandas as pd
import json
from scrapers.base import BaseScraper, BaseParser, BaseFilter

class BBC_Scraper(BaseScraper):
  def __init__(self) -> None:
     super().__init__()
     self.INDEX_URL = (
              "https://www.bbc.com/sitemaps/"
              "https-index-com-archive.xml" )

  def get_sitemap_urls(self):
    """
    Отримує URL усіх sitemap-ів з головного sitemap index
    """

    response = self.get_response(self.INDEX_URL)
    soup = BeautifulSoup(response, "xml")
    sitemap_urls = []

    for sitemap in soup.find_all("sitemap"):
        loc = sitemap.find("loc")
        if loc:
          sitemap_urls.append(loc.get_text(strip=True))

    return sitemap_urls

  def get_article_urls(self, sitemap_url):
    """
    Завантажує один sitemap і повертає список URL із /ukrainian/
    """
    print(f"Processing: {sitemap_url}") #
    response = self.get_response(sitemap_url)
    soup = BeautifulSoup(response, "xml")

    articles = []
    for url in soup.find_all("url"):
        loc = url.find("loc")
        if not loc:
            print('loc not found') #
            continue

        article_url = loc.get_text(strip=True)

        if "/ukrainian/" not in article_url: # /ukrainian/ filter
            continue

        articles.append(article_url)

    return articles

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


class BBC_ArticleParser(BaseParser):
  def __init__(self, source_name, target_years=range(2022, 2027)) -> None:
    self.target_years = target_years
    self.HEADERS = {"User-Agent": "Mozilla/5.0"}
    self.source_name = source_name
    self.stopwords_list = ["Автор фото",
                      "End of ",
                      "Хочете отримувати головн",
                      "Ваш пристрій не підтримує",
                      "Кінець Варто подивитися",
                      "© 2026 BBC",
                      "Усі епізоди",
                      "Подкаст BBC News",
                      "Кінець Подкаст",
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
    """
    Намагається знайти дату публікації.
    """

    # article:published_time
    meta = soup.find("meta", attrs={"name": "article:published_time"})

    if meta and meta.get("content"):
        return self._convert_date(meta["content"])

    # <time datetime="...">
    time_tag = soup.find("time")

    if time_tag:
        if time_tag.get("datetime"):
            return self._convert_date(time_tag["datetime"])

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


  def _get_article_text(self, soup):
    paragraphs = []

    # Основний варіант: параграфи всередині article
    article = soup.find("article")

    if article:
        for p in article.find_all("p"):
            text = p.get_text(
                " ",
                strip=True
            )

            if text:
                paragraphs.append(text)

    # якщо <article> не знайшовся

    if not paragraphs:

        for p in soup.find_all("p"):
            text = p.get_text(
                " ",
                strip=True
            )

            if text and not any([text.startswith(el) for el in self.stopwords_list]):
                paragraphs.append(text)

    # Прибираємо дублікати, але зберігаємо порядок
    paragraphs = list(
        dict.fromkeys(paragraphs)
    )

    article_text = "\n".join(
        paragraphs
    )
    return article_text


  def _get_article_title(self, soup):
    title = None

    # Основний варіант
    meta_title = soup.find(
        "meta",
        attrs={"property": "og:title"})

    if meta_title:
        title = meta_title.get("content")

    # Запасний варіант
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    return title

  def _get_tags(self, soup):
    tags = [
    tag.get("content")
    for tag in soup.find_all("meta", attrs={"name": "article:tag"})
    if tag.get("content")
      ]
    return tags

  def _get_description(self, soup):
    description_tag = (soup.find("meta", attrs={"name": "description"})
                      or soup.find("meta", attrs={"name": "twitter:description"})
                      )

    description = (description_tag.get("content") if description_tag else None)
    return description


STOPWORDS = ["Хочете отримувати",
             "Підписуйтеся на ",
             "Кінець X допису",
             "Контент недоступний",
             "Ця стаття містить контент",
             "І підписуйтеся на",
             "Кінець YouTube допису",
             "Дивіться наш YouTube",
             "Всі випуски",
             "Ви також можете подивитись",
             "У випуску",
             "Читайте також",
             "Огляд підготува",
             ]

class BBC_ArticleFilter(BaseFilter):
  def __init__(self) -> None:
     self.relevant_tags = {
         "Росія",
         "Дональд Трамп",
         "Володимир Путін",
         "Російсько-українська війна",
         "Володимир Зеленський",
         "Київ",
         "Economic sanctions",
         "Санкції проти Росії",
         "Донбас",
         "Окуповані Росією території",
         "Дрони",
         "Збройні сили",
         "Військова техніка",
         "Стосунки Росія — НАТО",
         "Військово-морські сили України",
     }
     self.stopwords_list = {
         "Наука",
         "Здоров'я",
         "Життя",
         "Освіта",
         "Вживання наркотиків",
         "Культура",
         "Мистецтво",
         "Дієта та харчування",
     }
     self.relevant_articles = []
     self.irrelevant_articles = []
     self.undefined_articles = []

  def clean_text(self, sentences):
      clean_sentences = [sent for sent in sentences if not any([sent.startswith(el) for el in STOPWORDS])]
      return "\n".join(clean_sentences)

  def filter(self, articles_file_path="parsed_articles.json", save_path="filtered_articles.json"):
    with open(articles_file_path, "r", encoding="utf-8") as file:
        parsed_articles = json.load(file)

    parsed_articles_clean = []
    for element in parsed_articles:
      if self.clean_text(element['text'].split('\n')):
        element['id'] = element['url'].split('/')[-1]
        element['text'] = self.clean_text(element['text'].split('\n'))
        parsed_articles_clean.append(element)

    articles_df = pd.DataFrame(parsed_articles_clean)
    df_filtered = articles_df[articles_df["tags"].apply(
        lambda tags: bool(set(tags) & self.relevant_tags))
                              ].copy()
    self.relevant_articles = df_filtered

    df_filtered.to_json(save_path, orient="records", force_ascii=False, indent=2)

    self.irrelevant_articles = articles_df[articles_df["tags"].apply(
        lambda tags: bool(set(tags) & self.stopwords_list) and not bool(set(tags) & self.relevant_tags))
                              ].copy()
    self.undefined_articles = articles_df[articles_df["tags"].apply(
        lambda tags: (not bool(set(tags) & self.stopwords_list))
                 and (not bool(set(tags) & self.relevant_tags)))
                              ].copy()

    print(f"Total articles: {len(parsed_articles)}")
    print(f"Articles with text: {len(articles_df)}")
    print(f"Relevant articles: {len(df_filtered)} ({len(df_filtered)/len(articles_df)*100}%)")
    print(f"Irrelevant articles: {len(self.irrelevant_articles)} ({len(self.irrelevant_articles)/len(articles_df)*100}%)")
    print(f"Undefined articles: {len(self.undefined_articles)} ({len(self.undefined_articles)/len(articles_df)*100}%)")

    return df_filtered