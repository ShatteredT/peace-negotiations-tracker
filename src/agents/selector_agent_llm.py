import json
import time
import sqlite3
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
from src.config import GEMINI_API_KEY, validate_config
from src.logger import get_logger

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

logger = get_logger("selector_agent_llm")

SELECTOR_PROMPT = """
Ти — фільтр новин про мирні переговори. Твоє завдання: визначити, чи стосується стаття МИРНИХ ПЕРЕГОВОРІВ між Україною та Росією (2022-2026).

Поверни JSON:
{
  "is_peace_related": true/false,
  "confidence": 0.0-1.0,
  "reason": "коротка причина"
}

Критерії TRUE:
- Прямі переговори/зустрічі делегацій
- Припинення вогню, перемир'я
- Обмін полоненими
- Безпекові гарантії, нейтральний статус
- Терitoriальні угоди/поступки
- Гуманітарні коридори
- Істанбул, Женева, Мінськ, Нормандський формат
- Заяви Зеленського/Путіна/Єрмака/МЗС про мир

Критерії FALSE:
- Лише бої/атаки/удари БЕЗ згадування переговорів
- Внутрішня політика, економіка, спорт
- Постачання зброї, санкції
- Судові процеси, розслідування воєнних злочинів
"""

class LLMPeaceClassifier:
    def __init__(self):
        validate_config()
        if not HAS_GENAI:
            raise RuntimeError("google-genai not installed")
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = "gemini-2.0-flash"
    
    def classify(self, title: str, text: str, max_retries: int = 5) -> dict:
        content = f"Title: {title}\nText: {text[:3000]}"
        
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=SELECTOR_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                result = json.loads(response.text)
                return result
                
            except Exception as e:
                err_str = str(e)
                is_retryable = "429" in err_str or "503" in err_str or "UNAVAILABLE" in err_str
                
                if is_retryable and attempt < max_retries:
                    wait_time = min(2 ** attempt * 5, 60)  # 10s, 20s, 40s, 60s max
                    logger.warning(f"Rate limit (429/503). Attempt {attempt}/{max_retries}. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"LLM classification failed: {e}")
                    raise
        
        return {"is_peace_related": False, "confidence": 0.0, "reason": "LLM failed after retries"}


class HybridSelectorAgent:
    def __init__(self, db_path: str = "data/selector_checkpoints.db", use_llm: bool = True):
        self.db_path = db_path
        self.use_llm = use_llm and HAS_GENAI
        self._init_db()
        if self.use_llm:
            self.llm = LLMPeaceClassifier()
        else:
            self.llm = None
            logger.warning("LLM not available, using keyword-only mode")
    
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
                    llm_confidence REAL,
                    llm_reason TEXT,
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
    
    def _save_selected(self, article: dict):
        import datetime
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO selected_articles 
                (id, source, title, date, url, text, matched_keywords, llm_confidence, llm_reason, selected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (article["id"], article["source"], article["title"], article["date"], 
                  article["url"], article["text"], json.dumps(article["matched_keywords"], ensure_ascii=False),
                  article.get("llm_confidence", 0.0), article.get("llm_reason", ""), 
                  datetime.datetime.now().isoformat()))
    
    def _keyword_filter(self, text: str) -> list[str]:
        matched = []
        for pattern in PEACE_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                matched.extend(matches if isinstance(matches[0], str) else [m[0] for m in matches])
        return list(set(matched))
    
    def filter_articles(self, articles: list[dict], source: str, target_count: int = 100,
                       keyword_threshold: int = 1) -> list[dict]:
        processed_ids, selected_count = self._load_checkpoint(source)
        logger.info(f"Resuming {source}: {selected_count} selected, {len(processed_ids)} processed")
        
        selected = []
        
        for article in articles:
            if selected_count >= target_count:
                break
            
            article_id = article.get("id", article.get("url", ""))
            if article_id in processed_ids:
                continue
            
            processed_ids.add(article_id)
            
            text = f"{article.get('title', '')} {article.get('description', '')} {article.get('text', '')}"
            matched_keywords = self._keyword_filter(text)
            
            # Stage 1: Keyword filter
            if len(matched_keywords) < keyword_threshold:
                if len(processed_ids) % 100 == 0:
                    self._save_checkpoint(source, processed_ids, selected_count)
                continue
            
            # Stage 2: LLM verification (optional)
            llm_confidence = 0.0
            llm_reason = ""
            
            if self.use_llm and len(matched_keywords) < 3:  # Only LLM for ambiguous cases
                try:
                    llm_result = self.llm.classify(article.get("title", ""), article.get("text", ""))
                    llm_confidence = llm_result.get("confidence", 0.0)
                    llm_reason = llm_result.get("reason", "")
                    
                    if not llm_result.get("is_peace_related", False) or llm_confidence < 0.5:
                        logger.info(f"LLM rejected: {article.get('title', '')[:50]} (conf: {llm_confidence})")
                        if len(processed_ids) % 100 == 0:
                            self._save_checkpoint(source, processed_ids, selected_count)
                        continue
                except Exception as e:
                    logger.warning(f"LLM failed, accepting on keywords: {e}")
            
            # Accept article
            result = {
                "id": article_id,
                "title": article.get("title", ""),
                "date": article.get("date", ""),
                "source": source,
                "url": article.get("url", ""),
                "text": article.get("text", ""),
                "matched_keywords": matched_keywords,
                "llm_confidence": llm_confidence,
                "llm_reason": llm_reason
            }
            selected.append(result)
            self._save_selected(result)
            selected_count += 1
            logger.info(f"Selected {selected_count}/{target_count} from {source}: {article.get('title', '')[:60]}")
            
            if len(processed_ids) % 50 == 0:
                self._save_checkpoint(source, processed_ids, selected_count)
        
        self._save_checkpoint(source, processed_ids, selected_count)
        logger.info(f"Finished {source}: selected {len(selected)}/{target_count}")
        return selected
    
    def export_results(self, output_path: str):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT id, source, title, date, url, text, matched_keywords, llm_confidence, llm_reason
                FROM selected_articles ORDER BY source, date
            """).fetchall()
        
        results = []
        for row in rows:
            results.append({
                "id": row[0], "source": row[1], "title": row[2], "date": row[3],
                "url": row[4], "text": row[5], "matched_keywords": json.loads(row[6]),
                "llm_confidence": row[7], "llm_reason": row[8]
            })
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"Exported {len(results)} articles to {output_path}")


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


if __name__ == "__main__":
    import re
    
    agent = HybridSelectorAgent(use_llm=True)
    
    # Load BBC
    bbc_path = Path("data/BBC/filtered.json")
    if bbc_path.exists():
        logger.info("Loading BBC articles...")
        with open(bbc_path, "r", encoding="utf-8") as f:
            bbc_articles = json.load(f)
        selected = agent.filter_articles(bbc_articles, "bbc", target_count=100)
        logger.info(f"BBC: selected {len(selected)} articles")
    
    agent.export_results("data/filtered_corpus.json")
    print("Done!")