import json
import re
import time
import sqlite3
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
from src.config import GEMINI_API_KEY, validate_config
from src.logger import get_logger

logger = get_logger("selector_agent")

PEACE_KEYWORDS = [
    r"мирн(?:и|а|і|ою|е)?\s+переговор(?:и|а|ами|ах)?",
    r"переговор(?:и|а|ами|ах)?\s+про\s+мир",
    r"мирна\s+угода",
    r"угод(?:а|у|ою|и|ах)?\s+про\s+припинення\s+вогню",
    r"припинення\s+вогню",
    r"перемир'я",
    r"демаркаційн(?:а|ою|і|е)?\s+ліні(?:я|ї|ю|ями|ях)?",
    r"міністерств(?:о|а|ом|у)?\s+закордонн(?:их|ої|ої)?\s+справ",
    r"заступник\s+глави\s+офісу\s+президента",
    r"Єрмак",
    r"Путін",
    r"Зеленський",
    r"макрон",
    r"шольц",
    r"байден",
    r"їстанбул",
    r"стамбул",
    r"генев(?:а|у|ою|і|е)?",
    r"мiнськ",
    r"нормандськ(?:ий|а|ому|ими|их)?\s+формат",
    r"контактна\s+група",
    r"торг(?:і|у|е)?\s+полоненими",
    r"обмін\s+полоненими",
    r"гуманітарн(?:ий|а|ою|і|е)?\s+коридор",
    r"безпеков(?:і|а|ою|ими|их)?\s+гаранті(?:я|ї|ям|ями)?",
    r"нейтральн(?:ий|а|ого|ої|ому|им|і|е)?\s+статус",
    r"неблоков(?:ий|а|е)?\s+статус",
    r"деокупаці(?:я|ію|ї|єю|іями)?",
    r"відновлення\s+територіальн(?:ої|ої)?\s+цілісност(?:і|ю|ї)?",
    r"кордони\s+1991",
    r"1991\s+рік\s+кордони",
]

PEACE_PATTERNS = [re.compile(kw, re.IGNORECASE) for kw in PEACE_KEYWORDS]

@dataclass
class FilteredArticle:
    id: str
    title: str
    date: str
    source: str
    url: str
    text: str
    matched_keywords: list[str]

class SelectorAgent:
    def __init__(self, db_path: str = "data/selector_checkpoints.db"):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    source TEXT PRIMARY KEY,
                    processed_ids TEXT,
                    selected_count INTEGER,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS selected_articles (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    date TEXT,
                    url TEXT,
                    text TEXT,
                    matched_keywords TEXT,
                    selected_at TEXT
                )
            """)
    
    def _save_checkpoint(self, source: str, processed_ids: set, selected_count: int):
        import datetime
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO checkpoints (source, processed_ids, selected_count, updated_at)
                VALUES (?, ?, ?, ?)
            """, (source, json.dumps(list(processed_ids)), selected_count, datetime.datetime.now().isoformat()))
    
    def _load_checkpoint(self, source: str) -> tuple[set, int]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT processed_ids, selected_count FROM checkpoints WHERE source = ?", (source,)).fetchone()
            if row:
                return set(json.loads(row[0])), row[1]
        return set(), 0
    
    def _save_selected(self, article: FilteredArticle):
        import datetime
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO selected_articles (id, source, title, date, url, text, matched_keywords, selected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (article.id, article.source, article.title, article.date, article.url, article.text,
                  json.dumps(article.matched_keywords, ensure_ascii=False), datetime.datetime.now().isoformat()))
    
    def _matches_peace_keywords(self, text: str) -> list[str]:
        matched = []
        for pattern in PEACE_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                matched.extend(matches if isinstance(matches[0], str) else [m[0] for m in matches])
        return list(set(matched))
    
    def filter_articles(self, articles: list[dict], source: str, target_count: int = 100, 
                       use_llm_fallback: bool = False) -> list[FilteredArticle]:
        processed_ids, selected_count = self._load_checkpoint(source)
        logger.info(f"Resuming {source}: {selected_count} already selected, {len(processed_ids)} processed")
        
        selected = []
        
        for article in articles:
            if selected_count >= target_count:
                break
                
            article_id = article.get("id", article.get("url", ""))
            if article_id in processed_ids:
                continue
            
            processed_ids.add(article_id)
            
            text = f"{article.get('title', '')} {article.get('description', '')} {article.get('text', '')}"
            matched = self._matches_peace_keywords(text)
            
            if matched:
                filtered = FilteredArticle(
                    id=article_id,
                    title=article.get("title", ""),
                    date=article.get("date", ""),
                    source=source,
                    url=article.get("url", ""),
                    text=article.get("text", ""),
                    matched_keywords=matched
                )
                selected.append(filtered)
                self._save_selected(filtered)
                selected_count += 1
                logger.info(f"Selected {selected_count}/{target_count} from {source}: {article.get('title', '')[:60]}")
            
            if len(processed_ids) % 100 == 0:
                self._save_checkpoint(source, processed_ids, selected_count)
        
        self._save_checkpoint(source, processed_ids, selected_count)
        logger.info(f"Finished {source}: selected {len(selected)}/{target_count}")
        return selected

    def export_results(self, output_path: str):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT id, source, title, date, url, text, matched_keywords 
                FROM selected_articles ORDER BY source, date
            """).fetchall()
        
        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "source": row[1],
                "title": row[2],
                "date": row[3],
                "url": row[4],
                "text": row[5],
                "matched_keywords": json.loads(row[6])
            })
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"Exported {len(results)} articles to {output_path}")


def load_bbc_articles(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_radiosvoboda_urls(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    articles = []
    for sitemap_url, urls in data.items():
        for url in urls:
            articles.append({"url": url, "source": "radiosvoboda", "id": url})
    return articles


if __name__ == "__main__":
    agent = SelectorAgent()
    
    # Load BBC
    bbc_path = Path("data/BBC/filtered.json")
    if bbc_path.exists():
        logger.info("Loading BBC articles...")
        bbc_articles = load_bbc_articles(str(bbc_path))
        selected = agent.filter_articles(bbc_articles, "bbc", target_count=100)
        logger.info(f"BBC: selected {len(selected)} articles")
    
    # Load RadioSvoboda (only URLs - would need scraping)
    rs_path = Path("data/RadioSvoboda/urls.json")
    if rs_path.exists():
        logger.info("Loading RadioSvoboda URLs...")
        rs_articles = load_radiosvoboda_urls(str(rs_path))
        logger.info(f"RadioSvoboda: {len(rs_articles)} URLs found (need scraping)")
    
    agent.export_results("data/filtered_corpus.json")
    print("Done!")