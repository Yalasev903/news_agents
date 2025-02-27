import os
import sqlite3
import yaml
import json
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool
from datetime import datetime
from crewai.memory import LongTermMemory
from crewai.memory.storage.ltm_sqlite_storage import LTMSQLiteStorage
from dotenv import load_dotenv

# Загружаємо змінні середовища з .env
load_dotenv("/var/www/zroby_sam_crewai/news_agents/.env")

# Ініціалізація інструмента пошуку
search_tool = SerperDevTool()

# Функція для збереження однієї новини в БД
def save_news_to_db(title, slug, excerpt, content, category_id, image_url):
    conn = sqlite3.connect('/var/www/zroby_sam/storage/database/zroby_sam.sqlite')
    cursor = conn.cursor()

    # Перевіряємо, чи існує запис із таким slug
    cursor.execute("SELECT COUNT(*) FROM news WHERE slug = ?", (slug,))
    count = cursor.fetchone()[0]
    if count > 0:
        # Якщо існує, генеруємо новий унікальний slug, додаючи метку часу
        slug = f"{slug}-{int(datetime.now().timestamp())}"

    cursor.execute("""
        INSERT INTO news (title, slug, excerpt, content, news_category_id, image_url, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (title, slug, excerpt, content, category_id, image_url, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"[DEBUG] Saved news: title='{title}', slug='{slug}', category_id={category_id}")

# Callback для обробки виводу задачі перед збереженням
def save_news_callback(output):
    print("[DEBUG] Запущено save_news_callback, output =", output)
    
    # Преобразуємо output у словник/список
    if hasattr(output, "model_dump"):
        output_data = output.model_dump()
    else:
        output_data = output

    print("[DEBUG] Отримані дані:", output_data)

    def process_news_item(news_item):
        title = (news_item.get('title') or news_item.get('Title') or '').strip()
        slug = (news_item.get('slug') or news_item.get('Slug') or '').strip()
        excerpt = (news_item.get('excerpt') or news_item.get('Excerpt') or '').strip()
        content = (news_item.get('content') or news_item.get('Content') or '').strip()
        # Перевіряємо варіанти ключа для категорії
        category_id = (news_item.get('category_id') or 
                       news_item.get('news_category_id') or 
                       news_item.get('neww_categori_id') or
                       news_item.get('new_categori_id') or 0)
        image_url = (news_item.get('image_url') or news_item.get('Image_url') or '').strip()
        print(f"[DEBUG] Обробляємо новину: title='{title}', slug='{slug}', category_id={category_id}")
        save_news_to_db(title, slug, excerpt, content, category_id, image_url)

    # Якщо output_data містить ключ 'raw'
    if isinstance(output_data, dict) and 'raw' in output_data:
        raw_value = output_data['raw']
        raw_str = raw_value.strip()
        # Видаляємо markdown-кодові огородження, якщо є
        if raw_str.startswith("```"):
            lines = raw_str.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw_str = "\n".join(lines).strip()
        print("[DEBUG] Очишчений raw текст:", raw_str)
        
        # Якщо рядок починається з '{' або '[', спробуємо отримати коректний JSON
        if raw_str.startswith('{') or raw_str.startswith('['):
            # Знаходимо останню закриваючу фігурну скобку
            last_brace = raw_str.rfind('}')
            if last_brace != -1:
                raw_str = raw_str[:last_brace+1]
            try:
                parsed = json.loads(raw_str)
            except Exception as e:
                print("[DEBUG] Помилка парсингу JSON:", e)
                parsed = None
            news_list = []
            if parsed:
                if isinstance(parsed, dict) and "news" in parsed:
                    news_list = parsed["news"]
                elif isinstance(parsed, list):
                    news_list = parsed
                else:
                    print("[DEBUG] Неочікуваний формат JSON:", parsed)
            else:
                print("[DEBUG] Не вдалося розпарсити JSON.")
        else:
            print("[DEBUG] Виявлено raw SQL, намагаємося виконати його.")
            start_idx = raw_str.find("INSERT INTO news")
            if start_idx != -1:
                sql_part = raw_str[start_idx:]
                end_idx = sql_part.find("Новини успішно збережені")
                if end_idx != -1:
                    sql_part = sql_part[:end_idx]
                if not sql_part.strip().endswith(";"):
                    sql_part = sql_part.strip() + ";"
                print("[DEBUG] SQL для виконання:\n", sql_part)
                try:
                    conn = sqlite3.connect('/var/www/zroby_sam/storage/database/zroby_sam.sqlite')
                    conn.executescript(sql_part)
                    conn.commit()
                    conn.close()
                    print("[DEBUG] SQL скрипт успішно виконано.")
                except Exception as e:
                    print("[DEBUG] Помилка при виконанні SQL скрипта:", e)
                return

        print("[DEBUG] Розпарсений JSON (news_list):", news_list)
        # Фільтрація: для кожної категорії вибираємо першу новину, де довжина content ≥ 1000 символів
        selected = {}
        for item in news_list:
            cat = item.get("new_categori_id") or item.get("new_categori_id".lower())
            if not cat:
                continue
            content = item.get("content") or ""
            if len(content) < 200:
                continue
            if cat not in selected:
                selected[cat] = item
        result_news = list(selected.values())
        print("[DEBUG] Відобрані новини по категоріях:", result_news)
        if not result_news:
            print("[DEBUG] Немає новин, що задовольняють критерії (content ≥1000 символів).")
        else:
            for news_item in result_news:
                process_news_item(news_item)
        return

    # Якщо дані вже структуровані (як список чи словник без 'raw')
    if isinstance(output_data, list):
        for news_item in output_data:
            process_news_item(news_item)
    else:
        process_news_item(output_data)

@CrewBase
class NewsAgents:
    """Команда агентів для обробки новин"""

    def __init__(self):
        # Завантаження конфігурацій агентів та задач з YAML-файлів
        base_dir = os.path.dirname(__file__)
        config_dir = os.path.join(base_dir, "config")
        with open(os.path.join(config_dir, 'agents.yaml'), 'r', encoding='utf-8') as f:
            self.agents_config = yaml.safe_load(f)
        with open(os.path.join(config_dir, 'tasks.yaml'), 'r', encoding='utf-8') as f:
            self.tasks_config = yaml.safe_load(f)

    @agent
    def researcher(self) -> Agent:
        return Agent(
            config=self.agents_config['researcher'],
            verbose=True,
            tools=[search_tool],
        )

    @agent
    def reporting_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config['reporting_analyst'],
            verbose=True
        )

    @agent
    def db_publisher(self) -> Agent:
        return Agent(
            config=self.agents_config['db_publisher'],
            verbose=True
        )

    @task
    def research_task(self) -> Task:
        return Task(
            config=self.tasks_config['research_task'],
        )

    @task
    def reporting_task(self) -> Task:
        return Task(
            config=self.tasks_config['reporting_task'],
            output_file='report.json'
        )

    @task
    def publishing_task(self) -> Task:
        return Task(
            config=self.tasks_config['publishing_task'],
            memory=True,
            callback=save_news_callback
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=[self.researcher(), self.reporting_analyst(), self.db_publisher()],
            tasks=[self.research_task(), self.reporting_task(), self.publishing_task()],
            process=Process.sequential,
            verbose=True,
            memory=True,
            long_term_memory=LongTermMemory(
                storage=LTMSQLiteStorage(db_path="/var/www/zroby_sam/storage/database/zroby_sam.sqlite")
            )
        )
