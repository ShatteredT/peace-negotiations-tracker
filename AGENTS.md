# AGENTS.md — peace-negotiations-tracker (Peacetalk Tracker)

## Project Overview
**Peacetalk Tracker** — мультиагентна NLP-система з веб-інтерфейсом для діахронічної оцінки та візуалізації динаміки мирних переговорів (2022–2026) на основі новин BBC Ukrainian, Українська Правда, The Guardian (~300 статей).

**Стек**: Python 3.11, Streamlit (UI), SQLite (лок. БД), Google Gemini 2.0 Flash API (google-genai, Free Tier 15 RPM), Pytest, GitHub Actions (CI/CD), Atlassian Jira + Confluence.

**Команда (3 особи):**
1. **AI Core Lead & System Architect** — промпти, логіка агентів, JSON-валідація, каркас Streamlit
2. **NLP Data Lead & Product Owner** — семантичне ядро, парсинг BBC/УП/Guardian, очищення корпусу, беклог
3. **DevOps, Security & Quality Lead** — Jira/Confluence, GitHub Actions, секрети, моделювання інциденту, міграції БД

---

## Two-Loop Architecture (Два контура)

### Контур А: Офлайн Batch AI Pipeline (Multi-Agent)
Послідовний запуск 3 агентів перед стартом додатку:
```
[News Sources] → [Scraping] → [Raw Corpus] → Agent 0 (Selector) → [Filtered Corpus ~300] → Agent 1 (Analyst) → Agent 2 (Synthesizer) → [SQLite]
```

- **Agent 0 — Selector** (TODO): відбирає 100 релевантних статей з тисяч на кожному сайті (BBC, УП, Guardian) за строгими регулярками/ключовими словами про мирні переговори. Вхід: сирий корпус; вихід: відфільтрований JSON/CSV (~300 статей).
- **Agent 1 — Analyst** (`src/agents/analyst_agent.py`): оцінює риторику -1.0 (ескалація) → +1.0 (компроміс) по 4 категоріях (дипломатія, території, безпека, гуманітарні). Повертає `confidence: 0.0-1.0` (self-consistency). Strict JSON schema via `response_schema=ArticleAnalysis`. Rate limit: `sleep(3-4s)`.
- **Agent 2 — Synthesizer**: агрегує місячні/квартальні показники з `confidence ≥ 0.7` → генерує структуровані аналітичні підсумки (Summary) для кожного періоду.
- **Validation Script**: простий Python-скрипт перевіряє `confidence ∈ [0.0, 1.0]` і відсіває низькоуверенні записи перед Синтезатором.

**Rate-Limiting**: черга з `sleep(4.5s)` + чекпоінти в SQLite для відновлення прогресу.

### Контур Б: Streamlit Web UI (Expert Interaction)
Зчитує готові дані з SQLite:
1. **Інтерактивний Plotly Timeline** (2022-2026) з фільтрами за роками/категоріями
2. **Текстові підсумки періодів** від Агента 2
3. **Експертні замітки (ExpertNote)** — головна керована сутність + RBAC

---

## Domain Model & RBAC

**ExpertNote** (основна сутність):
```
id, event_date, metric_category, content, author_id, importance_type, status, created_at
```

**Ролі (перемикач у sidebar без зовнішньої реєстрації):**
- **Analyst (Дослідник)**: перегляд графіків/висновків, CRUD **своїх** заміток, не може видаляти чужих (403)
- **Admin (Головний редактор)**: повний доступ, ексклюзивне право модерації/публікації OFFICIALSTATEMENT

**Domain Invariant**: один користувач ≠ більше однієї активної замітки на однакову `event_date` (unique constraint).

---

## Version Evolution: v1.0 → v2.0 (Change Request)

| | **v1.0 (Sprint 1)** | **v2.0 (Sprint 2 — Change Request)** |
|---|---|---|
| **Графік** | Статичний, дані агентів | Той самий |
| **Замітки** | CRUD, одразу `PUBLISHED` | `OFFICIALSTATEMENT` → `PENDING_APPROVAL` |
| **Модерація** | Немає | Admin: `APPROVED` / `REJECTED` |
| **Права** | Автор видаляє своє | Звичайний користувач **не може** погоджувати |
| **Міграція БД** | — | Додати `moderatedby`, `moderatedat`, `moderatorcomment`; старі замітки лишаються `PUBLISHED` без ретроспективної модерації |

---

## Lab Requirements (Вимоги до лабораторної) — MUST FOLLOW

**7 Контрольних точок (КТ):**
| КТ | Зміст | Результат |
|---|---|---|
| КТ-1 | Команда й постановка | Угода, ролі, репозиторій, Jira дошка, Confluence навігація |
| КТ-2 | Вимоги та архітектура | Історії, критерії, моделі, 2 ADR, план, реєстр ризиків |
| КТ-3 | Перший інкремент | Наскрізний сценарій, рев'ю, працюючий CI |
| КТ-4 | Базовий випуск v1.0 | Усі функції, тести, артефакт, інструкція запуску |
| КТ-5 | Навчальна експлуатація | Проведений інцидент (симуляція збою БД), хронологія, відновлення, коригувальні задачі |
| КТ-6 | Модифікація v2.0 | Аналіз впливу, рефакторинг, сумісна міграція, перевірена v2.0 |
| КТ-7 | Передавання й захист | Перевірка запуску іншим виконавцем, фінальний пакет, індивідуальні відповіді |

**Обов'язкові артефакти:**
- **Jira**: Scrum-процес, 2 спринти, 58 SP, колонки To Do→In Progress→Review→Done, гілки `feature/PT-<id>-name`, PR з ключем задачі
- **Confluence**: Головна сторінка, Командна угода, DoD, Blueprint, ADR-01 (SQLite), ADR-02 (Gemini Flash), Реєстр 5 ризиків, хронологія інциденту
- **GitHub Actions CI**: `flake8` + `pytest` (тести прав доступу, захист від дублікатів, валідація схем)
- **Тести**: Unit (валідація інтервалів, стани), Integration (реальне сховище, паралельні запити), E2E (повний шлях create→view→cancel), Access Control, Regression (v1.0 scenes work in v2.0)
- **Інцидент**: симуляція збою (недоступність БД/конфіг), розподілені ролі, хронологія, ≥2 гіпотези, ≥2 коригувальні задачі (одна реалізована до захисту)
- **Міграція v1.0→v2.0**: регресійні тести, рефакторинг, міграція даних, оновлення ADR/контрактів/тестів, smoke-тест, тег v2.0

**Оцінка**: 60 балів макс (42 командні + 18 індивідуальні). Захист: 25-30 хв (демо v2.0, простежуваність Confluence→Jira→PR→CI→release, інцидент, особисті пояснення).

---

## Current Codebase State

```
src/
  agents/
    analyst_agent.py         # Agent 1: Analyst (IMPLEMENTED)
    analyst_schemas.py       # Pydantic: ArticleAnalysis, CategoryScore
    selector_agent.py        # Agent 0: Selector - keyword-based filtering (IMPLEMENTED)
    selector_agent_llm.py    # Agent 0 (Hybrid): keyword + LLM verification with retry logic
    # TODO: synthesizer_agent.py
  ui/
    app.py                   # Streamlit MVP: Timeline + Expert Notes CRUD + RBAC (IMPLEMENTED)
  config.py                  # .env → GEMINI_API_KEY validation
  logger.py                  # Structured logging → logs/ai_pipeline.log
data/
  raw_mock_articles.json     # 2 mock articles for testing
  analyzed_articles.json     # Output from Agent 1
  peacetalk.db               # SQLite DB (news_articles, assessment_records, expert_notes)
  BBC/filtered.json          # 9320 articles from BBC (from PT-28)
  RadioSvoboda/urls.json     # Sitemap URLs (need scraping)
  backups/                   # Auto-backups for incident recovery
scrapers/                    # PT-28 branch: BBC/Радіо Свобода parsers
scripts/
  init_db.py                 # DB schema + constraints test (PT-13-16)
  simulate_incident.py       # Incident simulation КТ-5 (PT-37-40)
  auto_backup.py             # Corrective action CA-1
  health_check.py            # Corrective action CA-2
tests/
  test_analyst_agent.py      # Pydantic boundary validation only
.github/workflows/
  ci.yml                     # CI/CD: flake8 + pytest + schema + db-migration
```

**Branches / PRs:**
- `main` — base
- `feature/PT-6-analyst-agent-prompt` — current (Agent 1 + tests) — **PR #1 OPEN**
- `origin/PT-28-збір-та-підготовка-корпусу-новин` — scrapers + corpus — **PR #2 OPEN** (~970k lines, not merged)

---

## Key Commands

```bash
# Run Agent 1 on mock data
python -m src.agents.analyst_agent

# Run tests
pytest tests/

# Validate config
python -m src.config

# Install deps (requirements.txt is empty — install manually)
pip install google-genai pydantic python-dotenv pytest pdfplumber streamlit plotly
```

---

## Gotchas & Constraints

- **`requirements.txt` is empty** — must install deps manually
- **Gemini model hardcoded** as `gemini-3.6-flash` in `analyst_agent.py:31` (should be `gemini-2.0-flash` per blueprint)
- **Rate limit**: 3s sleep between calls (free tier) — blueprint says 4.5s for 15 RPM
- **Retries**: 503/429 → exponential backoff (5s, 10s)
- **Output schema**: enforced via `response_schema=ArticleAnalysis`
- **No SQLite integration yet** — Agent 1 writes to JSON file only
- **No Agent 0/2, no Streamlit UI, no Jira/Confluence setup** — all TODO
- **Agent 1 needs `confidence` field** in schema + validation script
- **PT-28 scrapers** in separate branch — need merge/integration for real corpus
- **Lab compliance**: every change must trace Confluence→Jira→PR→CI→Release; 2 ADR required; incident simulation mandatory

---

## Next Priority Steps (per Blueprint + Lab Requirements)

1. **Merge PT-28** (or integrate scrapers) → real corpus (~300 articles)
2. **Run Agent 0 (Selector)** on BBC corpus → filtered ~100 articles → `data/filtered_corpus.json`
3. **Add `confidence` field** to `ArticleAnalysis` schema + validation script (filter `confidence < 0.7`)
4. **Implement Agent 2 (Synthesizer)** + Batch Pipeline orchestrator with SQLite checkpoints
5. **Populate SQLite** with filtered corpus + Agent 1 assessments (run batch pipeline)
6. **Test Streamlit MVP** with real data (PT-9-12)
7. **RBAC UI polishing** (PT-33-35) - role switcher, archive, access tests
8. **Setup Jira/Confluence/GitHub Actions** per lab requirements
9. **Write tests** (unit/integration/e2e/access/regression) + CI pipeline
10. **Execute v2.0 Change Request** (approval workflow + migration)
11. **Prepare handover package** + defense materials