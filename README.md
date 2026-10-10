# Peacetalk Tracker

Діахронічний аналіз динаміки мирних переговорів Україна–Росія (2022–2026). LLM-агенти (Gemini) оцінюють
новини за шкалою від **−1.0 (ескалація)** до **+1.0 (компроміс)** за чотирма категоріями —
**diplomacy, security, territorial, humanitarian** — а Streamlit-застосунок показує динаміку на графіках
і дає експертам додавати власні замітки з модерацією.

## Поточний стан

| | |
|---|---|
| Джерело корпусу | BBC News Україна (інші джерела — у планах) |
| Відібрано та проаналізовано | 150 статей (26.02.2022 – 23.09.2026) |
| Оцінок у БД | 600 (4 категорії × 150) |
| Agent 1 — Analyst | ✅ готовий |
| Agent 0 — Selector | ✅ готовий (корпус BBC береться з `data/BBC/filtered.json` або з git) |
| Agent 2 — Synthesizer | ⏳ не реалізований (вкладка «Підсумки» — статистичне зведення без LLM) |
| Автентифікація | ⏳ ролі Analyst/Admin перемикаються вручну в сайдбарі |

## Швидкий старт

Усі команди запускаються з кореня проєкту.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # впишіть GEMINI_API_KEY (потрібен лише агентам)

python -m scripts.load_to_db                            # створює data/peacetalk.db з JSON
streamlit run src/ui/app.py
```

## Конвеєр

| Крок | Команда | Результат |
|------|---------|-----------|
| 1. Відбір статей (Selector) | `python -m src.agents.selector_optimized` | `data/filtered_corpus_optimized.json` |
| 2. Аналіз (Analyst) | `python -m src.agents.analyst_agent` | `data/analyzed_articles.json` |
| 3. Завантаження в БД | `python -m scripts.load_to_db` | `data/peacetalk.db` |
| 4. Інтерфейс | `streamlit run src/ui/app.py` | — |

Analyst обробляє статті пачками по 15 і підтримує resume: повторний запуск оцінює лише ще не оброблені статті.
Завантажувач ідемпотентний. Якщо в оцінці немає `confidence` (старі дані), у БД пишеться `NULL`, а не вигадане число.

## Структура

```
src/
  agents/        analyst_agent.py, analyst_schemas.py, selector_optimized.py
  ui/app.py      Streamlit: Огляд · Статті · Замітки · Підсумки
  config.py      шляхи, GEMINI_API_KEY, назви моделей
  logger.py
scripts/         init_db.py, load_to_db.py, export_gemini_full.py
tests/           схеми, обмеження БД, завантажувач, конфіг
data/            локальні дані (у .gitignore!): JSON, peacetalk.db, gemini_export_full/
.github/workflows/ci.yml   flake8 + pytest
```

## Налаштування (.env)

| Змінна | Призначення |
|--------|-------------|
| `GEMINI_API_KEY` | ключ Google AI Studio (лише для Selector/Analyst) |
| `ANALYST_MODEL` | модель Analyst (за замовчуванням `gemini-3.5-flash-lite`) |
| `SELECTOR_MODEL` | модель Selector (за замовчуванням `gemini-3.8-flash`) |

## Тести та CI

```bash
python -m pytest
flake8 src/ tests/ --select=E9,F63,F7,F82
```

Тести працюють з тимчасовою БД і не потребують ключа API чи заздалегідь створеної `data/peacetalk.db`.

## ⚠️ Дані поза git

Папки `data/`, `info/`, `helpers/` та `*.db` у `.gitignore`: git їх **не зберігає**. Робіть окремі резервні копії
(результати Gemini коштують квоти). Правила для агентів — у `AGENTS_RULES.md`.
