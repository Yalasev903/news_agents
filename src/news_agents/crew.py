import os
import sqlite3
import yaml
import json
import re
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool, DallETool
from datetime import datetime
from crewai.memory import LongTermMemory
from crewai.memory.storage.ltm_sqlite_storage import LTMSQLiteStorage
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv("/var/www/zroby_sam_crewai/news_agents/.env")

# Инициализация инструментов
search_tool = SerperDevTool()
dalle_tool = DallETool()

def save_news_to_db(title, slug, excerpt, content, category_id, image_url):
    conn = sqlite3.connect('/var/www/zroby_sam/storage/database/zroby_sam.sqlite')
    cursor = conn.cursor()
    # Проверяем, существует ли новость с таким slug
    cursor.execute("SELECT COUNT(*) FROM news WHERE slug = ?", (slug,))
    count = cursor.fetchone()[0]
    if count > 0:
        slug = f"{slug}-{int(datetime.now().timestamp())}"
    cursor.execute("""
        INSERT INTO news (title, slug, excerpt, content, news_category_id, image_url, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (title, slug, excerpt, content, category_id, image_url, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"[DEBUG] Saved news: title='{title}', slug='{slug}', category_id={category_id}")

def load_news_from_report():
    try:
        with open('report.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Если данные представлены в виде словаря с ключом 'news', то берем его,
        # иначе, если это список, возвращаем его
        if isinstance(data, dict):
            news_list = data.get('news', [])
        elif isinstance(data, list):
            news_list = data
        else:
            news_list = []
        print("[DEBUG] Загружены новости из report.json:", news_list)
        return news_list
    except Exception as e:
        print("[DEBUG] Не удалось загрузить report.json:", e)
        return []

def save_news_callback(output):
    print("[DEBUG] Запущено save_news_callback, output =", output)
    
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
        category_id = (news_item.get('category_id') or 
                       news_item.get('news_category_id') or 
                       news_item.get('neww_categori_id') or
                       news_item.get('new_categori_id') or 0)
        
        # Генерируем изображение через DALL‑E Tool
        prompt = (f"Generate a high quality news illustration image for the news titled '{title}'. "
                  "Return only the image URL in your response.")
        try:
            generated_response = dalle_tool.run(prompt=prompt)
            print(f"[DEBUG] Відповідь DALL‑E: {generated_response} (тип: {type(generated_response)})")
            if isinstance(generated_response, dict) and "data" in generated_response:
                image_url = generated_response["data"][0]["url"]
            elif isinstance(generated_response, str):
                image_url = generated_response.strip()
            else:
                image_url = ""
            # Если полученное значение не является корректным URL, используем запасное значение
            if not image_url.startswith("http"):
                print(f"[DEBUG] Отримано некоректне значення image_url від DALL‑E: {image_url}. Використовуємо запасне значення.")
                image_url = (news_item.get('image_url') or news_item.get('Image_url') or '').strip()
            print(f"[DEBUG] Згенеровано зображення для '{title}': {image_url}")
        except Exception as e:
            print("[DEBUG] Помилка генерації зображення через DALL‑E:", e)
            image_url = (news_item.get('image_url') or news_item.get('Image_url') or '').strip()
        
        print(f"[DEBUG] Обробляємо новину: title='{title}', slug='{slug}', category_id={category_id}")
        save_news_to_db(title, slug, excerpt, content, category_id, image_url)

    # Обработка output, если присутствует ключ 'raw'
    if isinstance(output_data, dict) and 'raw' in output_data:
        raw_str = output_data['raw'].strip()
        # Убираем блоки markdown, если они есть
        if raw_str.startswith("```"):
            lines = raw_str.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw_str = "\n".join(lines).strip()
        print("[DEBUG] Очищенный raw текст:", raw_str)
        
        news_list = []
        # Если текст начинается с '{' или '[', пробуем разобрать как JSON
        if raw_str.startswith("{") or raw_str.startswith("["):
            try:
                news_list = json.loads(raw_str)
            except Exception as e:
                print("[DEBUG] Ошибка парсинга JSON:", e)
        else:
            # Если raw текст не похож на JSON, пробуем загрузить новости из report.json
            print("[DEBUG] Raw текст не содержит JSON. Пытаемся загрузить новости из report.json.")
            news_list = load_news_from_report()

        # Если получены новости в виде словаря, пытаемся извлечь по ключу
        if isinstance(news_list, dict):
            if "news" in news_list:
                news_list = news_list["news"]
            elif "новини" in news_list:
                news_list = news_list["новини"]
            else:
                news_list = []
        print("[DEBUG] Розпарсений JSON (news_list):", news_list)
        # Фильтрация новостей по категориям (сниженный порог длины контента)
        selected = {}
        for item in news_list:
            cat = item.get("new_categori_id") or item.get("new_categori_id".lower())
            if not cat:
                continue
            item_content = item.get("content") or ""
            # Если условие на длину контента слишком жёсткое, порог можно снизить
            if len(item_content) < 400:
                continue
            if cat not in selected:
                selected[cat] = item
        result_news = list(selected.values())
        print("[DEBUG] Відобрані новини по категоріях:", result_news)
        if not result_news:
            print("[DEBUG] Немає новин, що задовольняють критерії (content ≥100 символів).")
        else:
            for news_item in result_news:
                process_news_item(news_item)
        return

    # Если output_data является списком, обрабатываем каждую новость
    if isinstance(output_data, list):
        for news_item in output_data:
            process_news_item(news_item)
    else:
        process_news_item(output_data)

@CrewBase
class NewsAgents:
    """Команда агентів для обробки новин"""

    def __init__(self):
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
            verbose=True,
            tools=[dalle_tool]
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
