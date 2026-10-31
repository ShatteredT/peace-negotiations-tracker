import re
import numpy as np
import pandas as pd

# Режим порівняння
WHOLE_WORD     = False   # True = лише слово повністю, False = підрядок (корінь слова)
CASE_SENSITIVE = False   # False = регістр не враховується

TITLE_LOGIC = "any"   # "any" = хоча б одне слово зі списку; "all" = усі слова одразу
TAG_LOGIC   = "any"

flags = 0 if CASE_SENSITIVE else re.IGNORECASE

def words_mask(series, words, logic):
    """Булева маска: рядок містить будь-яке з words (logic='any') або всі (logic='all')."""
    if not words:
        return None
    masks = []
    for w in words:
        p = re.escape(w.strip())
        if WHOLE_WORD:
            p = rf"(?<!\w){p}(?!\w)"
        masks.append(series.str.contains(p, flags=flags, regex=True, na=False))
    result = masks[0]
    for m in masks[1:]:
        result = (result & m) if logic == "all" else (result | m)
    return result

def filter_articles(ds, TITLE_WORDS, TAG_WORDS, COMBINE):

  meta = ds.select_columns(["title", "tags"]).to_pandas()
  meta["tags_str"] = meta["tags"].apply(lambda t: "\n".join(t))

  title_mask = words_mask(meta["title"], TITLE_WORDS, TITLE_LOGIC)
  tag_mask = words_mask(meta["tags_str"], TAG_WORDS, TAG_LOGIC)

  active = [m for m in (title_mask, tag_mask) if m is not None]
  if not active:
      raise ValueError("Задайте хоча б одне слово у TITLE_WORDS або TAG_WORDS")

  mask = active[0]
  for m in active[1:]:
      mask = (mask & m) if COMBINE == "and" else (mask | m)

  idx = np.flatnonzero(mask.to_numpy())
  filtered = ds.select(idx).to_pandas()

  print(f"Знайдено статей: {len(filtered):,} з {len(ds):,} ({len(filtered) / len(ds):.2%})")
  if title_mask is not None:
      print(f"  збіг у заголовках: {int(title_mask.sum()):,}")
  if tag_mask is not None:
      print(f"  збіг у тегах:      {int(tag_mask.sum()):,}")

  return filtered

def filter_by_date(df, date):
    dates = pd.to_datetime(df['date'])
    return df[dates >= pd.to_datetime(date)]

def save_to_file(filtered, path='filtered_articles'):
  if "date_dt" in filtered.columns:
    filtered = filtered.drop(columns=["date_dt"])
  elif "text_preview" in filtered.columns:
    filtered = filtered.drop(columns=["text_preview"])

  filtered["tags"] = filtered["tags"].apply(list)
  filtered.to_json(f"{path}.json", orient="records", force_ascii=False, indent=2)
  print('Файл із відфільтрованими статтями успішно збережено в json-файл.')