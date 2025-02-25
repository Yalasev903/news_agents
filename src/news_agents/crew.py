import os
import sqlite3
import yaml
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool
from datetime import datetime
from crewai.memory import LongTermMemory
from crewai.memory.storage.ltm_sqlite_storage import LTMSQLiteStorage
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv("/var/www/zroby_sam_crewai/news_agents/.env")

# Инициализация инструмента поиска
search_tool = SerperDevTool()

# Функция для сохранения новости в БД
def save_news_to_db(title, slug, excerpt, content, category_id, image_url):
    conn = sqlite3.connect('/var/www/zroby_sam/storage/database/zroby_sam.sqlite')
    cursor = conn.cursor()

    # Проверяем, существует ли уже запись с таким slug
    cursor.execute("SELECT COUNT(*) FROM news WHERE slug = ?", (slug,))
    count = cursor.fetchone()[0]
    if count > 0:
        # Если существует, генерируем новый уникальный slug, добавляя метку времени
        slug = f"{slug}-{int(datetime.now().timestamp())}"

    cursor.execute("""
        INSERT INTO news (title, slug, excerpt, content, news_category_id, image_url, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (title, slug, excerpt, content, category_id, image_url, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"[DEBUG] Saved news: title='{title}', slug='{slug}', category_id={category_id}")

# Callback для обработки вывода задачи перед сохранением
def save_news_callback(output):
    # Вывод для отладки
    print("[DEBUG] Запущен save_news_callback, output =", output)
    
    # Преобразуем output в словарь/список
    if hasattr(output, "model_dump"):
        output_data = output.model_dump()
    else:
        output_data = output

    print("[DEBUG] Полученные данные:", output_data)

    def process_news_item(news_item):
        # Получаем значения для каждого поля с учётом возможных вариантов ключей
        title = (news_item.get('title') or news_item.get('Title') or '').strip()
        slug = (news_item.get('slug') or news_item.get('Slug') or '').strip()
        excerpt = (news_item.get('excerpt') or news_item.get('Excerpt') or '').strip()
        content = (news_item.get('content') or news_item.get('Content') or '').strip()
        # Проверяем варианты для идентификатора категории:
        category_id = (news_item.get('category_id') or 
                       news_item.get('news_category_id') or 
                       news_item.get('neww_categori_id') or
                       news_item.get('new_categori_id') or 0)
        image_url = (news_item.get('image_url') or news_item.get('Image_url') or '').strip()

        print(f"[DEBUG] Обрабатываем новость: title='{title}', slug='{slug}', category_id={category_id}")
        save_news_to_db(title, slug, excerpt, content, category_id, image_url)

    # Если output_data является списком новостей
    if isinstance(output_data, list):
        for news_item in output_data:
            process_news_item(news_item)
    else:
        process_news_item(output_data)

@CrewBase
class NewsAgents:
    """Команда агентов для обработки новостей"""

    def __init__(self):
        # Загрузка конфигураций агентов и задач из YAML-файлов
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
            output_file='report.md'
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
