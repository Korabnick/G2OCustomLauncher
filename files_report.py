import os
import json
import hashlib
from pathlib import Path
import sys
import time

def calculate_fast_hash(filepath: str) -> str:
    """Быстрый, но достаточно надежный метод хеширования"""
    hasher = hashlib.blake2b(digest_size=32)
    
    try:
        with open(filepath, 'rb') as f:
            # Читаем только первые и последние 64KB файла
            hasher.update(f.read(65536))
            
            if os.path.getsize(filepath) > 131072:
                f.seek(-65536, os.SEEK_END)
                hasher.update(f.read())
            
            # Добавляем метаданные
            stat = os.stat(filepath)
            hasher.update(str(stat.st_size).encode())
            hasher.update(str(stat.st_mtime).encode())
            
    except Exception as e:
        print(f"\nОшибка чтения файла {filepath}: {str(e)}")
        return "error"
    
    return hasher.hexdigest()

def generate_directory_report(directory: str, output_file: str = 'directory_report.json'):
    """Генерация отчета с общим хешем директории"""
    start_time = time.time()
    
    if not os.path.isdir(directory):
        print(f"Ошибка: {directory} не является директорией")
        return

    report = {
        "files": [],
        "statistics": {
            "total_files": 0,
            "total_size": 0,
            "overall_hash": "",
            "scan_duration": 0
        },
    }

    # Подсчет общего количества файлов
    total_files = sum(len(files) for _, _, files in os.walk(directory))
    if total_files == 0:
        print("В указанной директории нет файлов для обработки")
        return

    print(f"Найдено файлов: {total_files}")
    processed_files = 0
    total_size = 0
    overall_hasher = hashlib.blake2b(digest_size=32)  # Хешер для общего хеша

    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            
            try:
                file_size = os.path.getsize(file_path)
                file_hash = calculate_fast_hash(file_path)
                
                if file_hash == "error":
                    continue
                
                relative_path = os.path.relpath(file_path, directory)
                display_path = file if root == directory else relative_path
                
                # Добавляем информацию о файле в отчет
                report["files"].append({
                    "path": display_path,
                    "size": file_size,
                    "hash": file_hash,
                    "modified": os.path.getmtime(file_path)
                })
                
                # Обновляем общий хеш
                overall_hasher.update(file_hash.encode('utf-8'))
                overall_hasher.update(relative_path.encode('utf-8'))  # Учитываем путь файла
                
                total_size += file_size
                processed_files += 1
                
                # Обновление прогресса
                if processed_files % 100 == 0 or processed_files == total_files:
                    sys.stdout.write(f"\rОбработано: {processed_files}/{total_files} ({processed_files/total_files:.1%}) "
                                   f"| Скорость: {processed_files/(time.time()-start_time):.1f} файл/сек")
                    sys.stdout.flush()
                    
            except Exception as e:
                print(f"\nОшибка обработки {file_path}: {str(e)}")
                continue

    # Финализация отчета
    report["statistics"]["total_files"] = processed_files
    report["statistics"]["total_size"] = total_size
    report["statistics"]["scan_duration"] = round(time.time() - start_time, 2)
    report["statistics"]["overall_hash"] = overall_hasher.hexdigest() if processed_files > 0 else ""
    
    # Сохранение отчета
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=4)

    print(f"\n\nОтчет сохранен в {output_file}")
    print(f"Успешно обработано: {processed_files}/{total_files} файлов")
    print(f"Общий размер: {total_size/1024/1024:.2f} MB")
    print(f"Общий хеш директории: {report['statistics']['overall_hash']}")
    print(f"Время сканирования: {report['statistics']['scan_duration']} сек")
    print(f"Скорость обработки: {processed_files/report['statistics']['scan_duration']:.1f} файл/сек")

if __name__ == "__main__":
    target_directory = "C:\\Users\\Icarus\\Desktop\\RGCLauncherPy\\test_files" # Директория для сканирования (полный путь до папки с игрой)
    output_filename = "directory_report.json"
    
    generate_directory_report(target_directory, output_filename)