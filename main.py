import flet as ft  # GUI фреймворк
import os
import subprocess  # Для запуска внешних процессов (игры)
import aiohttp  # Для HTTP-запросов (скачивание файлов)
from pathlib import Path  # Работа с путями файловой системы
import tomli  # Парсинг TOML конфига
from pydantic import BaseModel  # Валидация конфигурации
import logging  # Логирование
import shutil  # Работа с файлами
import json  # Работа с JSON
import hashlib  # Хеширование файлов
import asyncio  # Асинхронные операции
import sys
from time import sleep
import ctypes  # Для проверки прав администратора в Windows

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)

def is_admin():
    """Проверяет, запущен ли скрипт с правами администратора"""
    try:
        # Для Windows
        if sys.platform == 'win32':
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        # Для Linux/Mac (требует sudo)
        else:
            return os.getuid() == 0
    except Exception:
        return False

def elevate_admin():
    """Перезапускает скрипт с правами администратора"""
    if sys.platform == 'win32':
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, None, 1
        )
    else:
        print("Для Linux/Mac используйте sudo перед запуском")
    sys.exit()

# Проверка и повышение прав администратора при необходимости
if not is_admin():
    elevate_admin()

# Модели конфигурации с использованием Pydantic для валидации
class AppConfig(BaseModel):
    title: str  # Заголовок окна
    width: int  # Ширина окна
    height: int  # Высота окна

class BackgroundConfig(BaseModel):
    image: str  # Путь к фоновому изображению

class GameConfig(BaseModel):
    executable: str  # Путь к исполняемому файлу игры
    default_nickname: str  # Никнейм по умолчанию
    server_address: str  # Адрес сервера
    test_source_folder: str  # Тестовая папка с файлами (для разработки)
    test_download_folder: str  # Тестовая папка для загрузки
    files_manifest: str  # Путь к JSON с хешами файлов
    base_url: str  # Базовый URL для скачивания файлов

class ColorsConfig(BaseModel):
    primary: str  # Основной цвет интерфейса
    secondary: str  # Вторичный цвет
    text: str  # Цвет текста

class FontsConfig(BaseModel):
    main: str  # Основной шрифт
    size: int  # Размер шрифта

class Config(BaseModel):
    app: AppConfig
    background: BackgroundConfig
    game: GameConfig
    colors: ColorsConfig
    fonts: FontsConfig

def load_config() -> Config:
    """Загружает и валидирует конфигурацию из TOML файла"""
    with open("config.toml", "rb") as f:
        toml_data = tomli.load(f)
    return Config(**toml_data)

# Глобальный объект конфигурации
config = load_config()

def load_saved_nickname() -> str:
    """Загружает сохраненный никнейм из файла настроек"""
    try:
        with open("user_settings.json", "r") as f:
            data = json.load(f)
            return data.get("nickname", config.game.default_nickname)
    except (FileNotFoundError, json.JSONDecodeError):
        return config.game.default_nickname

def save_nickname(nickname: str):
    """Сохраняет никнейм в файл настроек"""
    data = {"nickname": nickname}
    with open("user_settings.json", "w") as f:
        json.dump(data, f)

def calculate_fast_hash(filepath: str) -> str:
    """Быстрое хеширование файла (первые и последние 64KB + метаданные)"""
    hasher = hashlib.blake2b(digest_size=32)
    
    try:
        with open(filepath, 'rb') as f:
            # Чтение только первых и последних 64KB файла для скорости
            hasher.update(f.read(65536))
            
            if os.path.getsize(filepath) > 131072:
                f.seek(-65536, os.SEEK_END)
                hasher.update(f.read())
            
            # Добавление метаданных для большей надежности
            stat = os.stat(filepath)
            hasher.update(str(stat.st_size).encode())
            hasher.update(str(stat.st_mtime).encode())
            
    except Exception as e:
        print(f"Ошибка чтения файла {filepath}: {str(e)}")
        return "error"
    
    return hasher.hexdigest()

class Downloader:
    """Класс для управления загрузкой и проверкой файлов игры"""
    def __init__(self, page: ft.Page):
        self.page = page  # Ссылка на страницу Flet для обновления UI
        self.download_queue = []  # Очередь файлов для загрузки
        self.current_download = None  # Текущий загружаемый файл
        self.total_progress = 0  # Общий прогресс загрузки
        self.file_progress = 0  # Прогресс текущего файла
        self.manifest = None  # Загруженный манифест файлов
        self.files_to_download = []  # Список файлов, требующих загрузки
        self.test_mode = True  # Режим тестирования (использует локальные файлы)
        self.source_folder = config.game.test_source_folder  # Источник файлов в тестовом режиме
        self.download_folder = config.game.test_download_folder  # Папка назначения
        # Элементы UI для отображения прогресса
        self.status_text = ft.Text("", color=config.colors.text)  # Статус текущего файла
        self.total_status_text = ft.Text("", color=config.colors.text)  # Общий статус
        self.file_progress_bar = None  # Прогресс-бар текущего файла
        self.total_progress_bar = None  # Общий прогресс-бар
        self.total_files = 0  # Всего файлов для проверки
        self.processed_files = 0  # Обработанных файлов
        self.total_size_bytes = 0  # Общий размер загрузки
        self.downloaded_bytes = 0  # Загружено байт
        self.files_requiring_download = 0  # Файлов, требующих загрузки
        self.size_requiring_download = 0  # Размер файлов для загрузки
        
    def load_manifest(self):
        """Загружает манифест файлов с их хешами"""
        try:
            with open(config.game.files_manifest, 'r', encoding='utf-8') as f:
                manifest_data = json.load(f)
                self.total_files = len(manifest_data.get("files", []))
                self.processed_files = 0
                return manifest_data
        except Exception as e:
            print(f"Ошибка загрузки манифеста: {str(e)}")
            self.total_files = 0
            return None
            
    async def check_files(self):
        """Асинхронная проверка файлов на соответствие манифесту"""
        self.manifest = self.load_manifest()
        if not self.manifest or not self.manifest.get("files"):
            self.status_text.value = "Ошибка: манифест не загружен или пуст"
            self.total_status_text.value = "Ошибка загрузки манифеста"
            self.update_progress()
            return []
            
        self.total_files = len(self.manifest["files"])
        self.processed_files = 0
        self.files_to_download = []
        self.files_requiring_download = 0
        self.size_requiring_download = 0
        
        # Проверка каждого файла из манифеста
        for file_info in self.manifest["files"]:
            file_path = Path(self.download_folder) / file_info["path"]
            expected_hash = file_info["hash"]
            file_size = file_info.get("size", 0)
            
            # Обновление UI
            self.processed_files += 1
            self.total_status_text.value = f"Проверка файлов: {self.processed_files}/{self.total_files}"
            
            # Проверка существования файла
            if not file_path.exists():
                self.status_text.value = f"{file_path.name} - отсутствует"
                self.files_to_download.append(file_info)
                self.files_requiring_download += 1
                self.size_requiring_download += file_size
            else:
                # Проверка хеша файла
                try:
                    current_hash = calculate_fast_hash(str(file_path))
                    if current_hash != expected_hash:
                        self.status_text.value = f"{file_path.name} - не прошел проверку"
                        self.files_to_download.append(file_info)
                        self.files_requiring_download += 1
                        self.size_requiring_download += file_size
                    else:
                        self.status_text.value = f"{file_path.name} - OK"
                except Exception as e:
                    print(f"Ошибка проверки файла {file_path}: {str(e)}")
                    self.status_text.value = f"{file_path.name} - ошибка проверки"
                    self.files_to_download.append(file_info)
                    self.files_requiring_download += 1
                    self.size_requiring_download += file_size
            
            self.update_progress()
            await asyncio.sleep(0)  # Даем циклу событий обработать UI
            
        return self.files_to_download

    def update_progress(self):
        """Обновляет прогресс-бары в UI"""
        if self.file_progress_bar:
            self.file_progress_bar.value = self.file_progress / 100
        if self.total_progress_bar:
            self.total_progress_bar.value = self.total_progress / 100
        self.page.update()
    
    def set_progress_bars(self, file_pb, total_pb):
        """Устанавливает ссылки на прогресс-бары для обновления"""
        self.file_progress_bar = file_pb
        self.total_progress_bar = total_pb
        
    async def add_download(self, file_info: dict):
        """Добавляет файл в очередь загрузки"""
        source_path = Path(self.source_folder) / file_info["path"] if self.test_mode else f"{config.game.base_url}/{file_info['path'].replace('\\', '/')}"
        destination = str(Path(self.download_folder) / file_info["path"])
        self.download_queue.append((str(source_path), destination, file_info.get("size", 0)))
        
    async def process_queue(self):
        """Обрабатывает очередь загрузки файлов"""
        total_download_size = sum(size for _, _, size in self.download_queue)
        self.total_size_bytes = total_download_size
        self.downloaded_bytes = 0
        
        while self.download_queue:
            source, destination, file_size = self.download_queue.pop(0)
            self.current_download = (source, destination, file_size)
            
            try:
                dest_path = Path(destination)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Обновление статуса
                files_left = len(self.download_queue)
                current_file_num = self.total_files - files_left
                self.total_status_text.value = (
                    f"Скачивание файлов: {current_file_num}/{self.total_files} | "
                    f"{self.downloaded_bytes/1024/1024:.1f}MB / {self.total_size_bytes/1024/1024:.1f}MB"
                )
                
                if self.test_mode:
                    # Локальное копирование с эмуляцией прогресса
                    source_path = Path(source)
                    if not source_path.exists():
                        self.status_text.value = f"Файл не найден: {source}"
                        self.update_progress()
                        continue
                        
                    total_size = os.path.getsize(source)
                    downloaded = 0
                    chunk_size = 1024 * 1024  # 1MB chunks
                    
                    try:
                        with open(source, 'rb') as src, open(destination, 'wb') as dst:
                            while True:
                                chunk = src.read(chunk_size)
                                if not chunk:
                                    break
                                dst.write(chunk)
                                downloaded += len(chunk)
                                self.downloaded_bytes += len(chunk)
                                self.file_progress = (downloaded / total_size) * 100
                                self.total_progress = (self.downloaded_bytes / self.total_size_bytes) * 100
                                
                                self.status_text.value = (
                                    f"{dest_path.name} - "
                                    f"{downloaded/1024/1024:.1f}MB / {total_size/1024/1024:.1f}MB"
                                )
                                self.update_progress()
                                await asyncio.sleep(0.01)  # Имитация задержки
                                
                    except Exception as copy_error:
                        self.status_text.value = f"Ошибка копирования: {str(copy_error)}"
                        if dest_path.exists():
                            dest_path.unlink()  # Удаление частичного файла
                        continue
                        
                else:
                    # Реальная загрузка по HTTP
                    async with aiohttp.ClientSession() as session:
                        async with session.get(source) as response:
                            if response.status != 200:
                                self.status_text.value = f"Ошибка загрузки {source}"
                                continue
                                
                            total_size = int(response.headers.get('content-length', 0))
                            downloaded = 0
                            
                            with open(destination, 'wb') as f:
                                async for data in response.content.iter_chunked(1024):
                                    f.write(data)
                                    downloaded += len(data)
                                    self.downloaded_bytes += len(data)
                                    self.file_progress = (downloaded / total_size) * 100
                                    self.total_progress = (self.downloaded_bytes / self.total_size_bytes) * 100
                                    
                                    self.status_text.value = (
                                        f"{dest_path.name} - "
                                        f"{downloaded/1024/1024:.1f}MB / {total_size/1024/1024:.1f}MB"
                                    )
                                    self.update_progress()
                
                # Проверка хеша после загрузки
                try:
                    downloaded_hash = calculate_fast_hash(destination)
                    expected_hash = next(
                        (f["hash"] for f in self.manifest["files"] 
                        if f["path"] == str(dest_path.relative_to(self.download_folder))),
                        None
                    )
                    
                    if downloaded_hash != expected_hash:
                        raise ValueError(f"Hash mismatch for {dest_path.name}")
                        
                except Exception as hash_error:
                    self.status_text.value = f"Ошибка проверки: {str(hash_error)}"
                    if dest_path.exists():
                        dest_path.unlink()
                    continue
                    
            except Exception as e:
                self.status_text.value = f"Критическая ошибка: {str(e)}"
                continue
                
            self.update_progress()
        
        # Завершение загрузки
        self.current_download = None
        self.status_text.value = "Все файлы успешно загружены"
        self.total_status_text.value = (
            f"Завершено: {self.files_requiring_download} файлов, "
            f"{self.size_requiring_download/1024/1024:.1f}MB"
        )
        self.update_progress()

def main(page: ft.Page):
    """Основная функция, создающая интерфейс лаунчера"""
    
    # Проверка прав администратора
    if sys.platform == 'win32' and not is_admin():
        page.snack_bar = ft.SnackBar(
            ft.Text("Требуются права администратора. Перезапускаем..."),
            bgcolor=ft.colors.ORANGE
        )
        page.snack_bar.open = True
        page.update()
        sleep(2)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, None, 1
        )
        sys.exit()

    # Настройки окна
    page.window_frameless = True  # Без рамки
    page.window_title_bar_hidden = True  # Скрыть заголовок
    page.window_bgcolor = ft.Colors.TRANSPARENT  # Прозрачный фон
    page.bgcolor = ft.Colors.TRANSPARENT
    
    page.window_width = config.app.width
    page.window_height = config.app.height
    page.window_min_width = config.app.width
    page.window_min_height = config.app.height
    page.window_opacity = 1.0
    
    # Функции управления окном
    def minimize_window(e):
        page.window_minimized = True
        page.update()

    def close_window(e):
        page.window.close()

    # Кнопки управления окном
    window_controls = ft.Row(
        [
            ft.IconButton(
                ft.icons.MINIMIZE,
                on_click=minimize_window,
                icon_size=16,
                style=ft.ButtonStyle(
                    color=config.colors.text,
                    overlay_color=ft.Colors.with_opacity(0.1, config.colors.text),
            )),
            ft.IconButton(
                ft.icons.CLOSE,
                on_click=close_window,
                icon_size=16,
                style=ft.ButtonStyle(
                    color=config.colors.text,
                    overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.RED)),
            ),
        ],
        spacing=0,
    )

    # Поле для ввода никнейма
    nickname_field = ft.TextField(
        label="Никнейм",
        value=load_saved_nickname(),
        width=300,
        height=40,
        text_size=config.fonts.size,
        color=config.colors.text,
        border_color=config.colors.primary,
        focused_border_color=config.colors.primary,
        text_align=ft.TextAlign.CENTER,
        bgcolor=ft.Colors.with_opacity(0.2, config.colors.primary),
        on_change=lambda e: save_nickname(nickname_field.value)
    )

    # Прогресс-бары
    file_progress_bar = ft.ProgressBar(width=300, color=config.colors.primary, value=0)
    total_progress_bar = ft.ProgressBar(width=300, color=config.colors.primary, value=0)

    # Инициализация Downloader
    downloader = Downloader(page)
    downloader.set_progress_bars(file_progress_bar, total_progress_bar)

    # Компоновка элементов прогресса
    progress_bars = ft.Column(
        [
            ft.Text("Общий прогресс:", size=12, color=config.colors.text),
            downloader.total_status_text,
            total_progress_bar,
            ft.Text("Текущий файл:", size=12, color=config.colors.text),
            downloader.status_text,
            file_progress_bar,
        ],
        spacing=5
    )

    # Основные кнопки
    play_button = ft.ElevatedButton(
        text="Играть",
        width=120,
        height=40,
        bgcolor=config.colors.primary,
        color=config.colors.text,
        disabled=False,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
    )

    check_files_button = ft.ElevatedButton(
        text="Проверить файлы",
        width=120,
        height=40,
        bgcolor=config.colors.primary,
        color=config.colors.text,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
    )

    def launch_game(e):
        """Запускает игру с указанным никнеймом"""
        try:
            nickname = nickname_field.value.strip()
            if not nickname:
                nickname = config.game.default_nickname
            
            game_path = Path(config.game.executable)
            logging.debug(f"Попытка запуска игры из: {game_path}")
            
            if not game_path.exists():
                error_msg = f"Файл игры не найден по пути: {game_path}"
                logging.error(error_msg)
                page.snack_bar = ft.SnackBar(
                    ft.Text(error_msg),
                    bgcolor=ft.Colors.RED
                )
                page.snack_bar.open = True
                page.update()
                return
            
            working_dir = Path(config.game.test_source_folder)
            logging.debug(f"Рабочая директория: {working_dir}")
            
            if not working_dir.exists():
                error_msg = f"Исходная папка не найдена: {working_dir}"
                logging.error(error_msg)
                page.snack_bar = ft.SnackBar(
                    ft.Text(error_msg),
                    bgcolor=ft.colors.RED
                )
                page.snack_bar.open = True
                page.update()
                return
            
            # Параметры командной строки для запуска игры
            cmd = [
                str(game_path),
                "--nickname", nickname,
                "--connect", config.game.server_address
            ]
            logging.debug(f"Команда запуска: {cmd}")
            
            # Запуск процесса игры
            process = subprocess.Popen(
                cmd,
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True
            )
            
            logging.debug(f"Процесс запущен с PID: {process.pid}")
            
        except Exception as e:
            error_msg = f"Ошибка при запуске игры: {str(e)}"
            logging.exception(error_msg)
            page.snack_bar = ft.SnackBar(
                ft.Text(error_msg),
                bgcolor=ft.colors.RED
            )
            page.snack_bar.open = True
            page.update()

    async def start_file_check(e):
        """Обработчик кнопки проверки файлов"""
        downloader = Downloader(page)
        downloader.set_progress_bars(file_progress_bar, total_progress_bar)
        
        # Обновление UI
        progress_bars.controls[1] = downloader.total_status_text
        progress_bars.controls[4] = downloader.status_text
        page.update()
        
        # Проверка файлов
        files_to_download = await downloader.check_files()
        
        if not files_to_download:
            downloader.total_status_text.value = f"Проверка завершена: все файлы актуальны ({downloader.total_files} файлов)"
            downloader.status_text.value = "Готово к запуску"
            play_button.disabled = False
            page.update()
            return
            
        downloader.total_status_text.value = (
            f"Найдено {downloader.files_requiring_download} файлов для загрузки "
            f"(из {downloader.total_files}) | "
            f"Общий размер: {downloader.size_requiring_download/1024/1024:.1f}MB"
        )
        downloader.status_text.value = "Подготовка к загрузке..."
        page.update()
        
        # Добавление файлов в очередь и загрузка
        for file_info in files_to_download:
            await downloader.add_download(file_info)
            
        await downloader.process_queue()
        
        play_button.disabled = False
        page.update()

    # Привязка обработчиков к кнопкам
    play_button.on_click = launch_game
    check_files_button.on_click = start_file_check

    # Заголовок окна с кнопками управления
    header = ft.WindowDragArea(
        ft.Container(
            ft.Row(
                [
                    ft.Text(config.app.title, size=16, color=config.colors.text),
                    window_controls,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=10, vertical=5),
            bgcolor=ft.Colors.TRANSPARENT,
        ),
        expand=False,
    )

    # Основное содержимое
    centered_content = ft.Column(
        [
            nickname_field,
            ft.Container(height=20),
            check_files_button,
            ft.Container(height=10),
            play_button,
            ft.Container(height=10),
            progress_bars,
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        expand=True,
    )

    # Компоновка основного интерфейса
    main_content = ft.Column(
        [
            header,
            ft.Divider(height=1, color=config.colors.primary),
            centered_content,
        ],
        expand=True,
    )

    # Основной контейнер
    main_container = ft.Container(
        main_content,
        width=config.app.width,
        height=config.app.height,
        bgcolor=ft.Colors.with_opacity(0.7, config.colors.secondary),
        border_radius=5,
        padding=5,
    )

    # Фоновое изображение
    background = ft.Image(
        src=config.background.image,
        width=config.app.width,
        height=config.app.height,
        fit=ft.ImageFit.COVER,
    )

    # Корневой элемент интерфейса
    root = ft.Stack(
        [
            background,
            main_container,
        ],
        width=config.app.width,
        height=config.app.height,
    )

    page.add(root)

if __name__ == "__main__":
    ft.app(
        target=main,
        view=ft.FLET_APP,
        assets_dir="static"
    )