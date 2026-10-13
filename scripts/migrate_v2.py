import sqlite3
import os
import sys

# Додаємо корінь проєкту до шляху пошуку модулів
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import DB_PATH

def migrate_database():
    """
    Скрипт міграції бази даних для v2.0 (PT-43, PT-44).
    Додає колонки модерації до таблиці експертних заміток
    та встановлює значення за замовчуванням для існуючих записів.
    """
    db_path = str(DB_PATH)
    print(f"[*] Початок міграції бази даних за шляхом: {db_path}")

    if not os.path.exists(db_path):
        print(f"[!] Увага: Файл бази даних не знайдено за шляхом {db_path}. Створюється нова структура.")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.close()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Перевіримо, які таблиці існують у БД
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"[*] Знайдені таблиці в БД: {tables}")

    target_table = None
    for t in ['expert_notes', 'notes', 'analyses', 'articles']:
        if t in tables:
            target_table = t
            break

    if not target_table:
        print("[*] Таблиць для заміток не знайдено. Створюємо базову таблицю expert_notes для демонстрації міграції.")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS expert_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'PUBLISHED',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        target_table = 'expert_notes'

    print(f"[*] Використовуємо цільову таблицю для міграції: {target_table}")

    # Отримуємо список існуючих колонок у таблиці
    cursor.execute(f"PRAGMA table_info({target_table});")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"[*] Існуючі колонки: {columns}")

    # Додаємо колонки review_status, reviewed_by, reviewed_at, якщо їх ще немає
    columns_to_add = [
        ("review_status", "TEXT DEFAULT 'APPROVED'"),
        ("reviewed_by", "TEXT DEFAULT NULL"),
        ("reviewed_at", "TEXT DEFAULT NULL")
    ]

    for col_name, col_definition in columns_to_add:
        if col_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE {target_table} ADD COLUMN {col_name} {col_definition};")
                print(f"[+] Успішно додано колонку: {col_name}")
            except sqlite3.OperationalError as e:
                print(f"[!] Помилка при додаванні колонки {col_name}: {e}")
        else:
            print(f"[*] Колонка {col_name} вже існує.")

    # PT-44: Міграція наявних записів (встановлення review_status = 'APPROVED' для старих записів)
    cursor.execute(f"UPDATE {target_table} SET review_status = 'APPROVED' WHERE review_status IS NULL;")
    conn.commit()
    print("[+] Міграція наявних записів завершена: старі записи отримали статус 'APPROVED'.")

    # Перевірка результату міграції
    cursor.execute(f"SELECT id, review_status FROM {target_table} LIMIT 5;")
    sample_rows = cursor.fetchall()
    print(f"[*] Зразки записів після міграції (id, review_status): {sample_rows}")

    conn.close()
    print("[✓] Базу даних успішно оновлено та перевірено до версії v2.0!")

if __name__ == "__main__":
    migrate_database()
