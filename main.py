import os
import re
import sys
import threading
import time
import winreg
from datetime import datetime, timedelta


class SteamSpeedDownloadMonitor:
    def __init__(self):
        self.steam_path = self.get_steam_path()
        self.log_file = os.path.join(self.steam_path, 'logs', 'content_log.txt')
        self.current_game = 'Нет активных загрузок'
        self.current_app_id = None
        self.last_position = 0
        self.running = True
        self.download_speed = "0.0 Mbps"
        self.status = "Нет активных загрузок"
        self.last_update = datetime.now()
        self.download_active = False
        self.paused = False
        self.speed_history = []
        self.downloaded_bytes = 0
        self.total_bytes = 0

    def get_steam_path(self):
        """Получает информацию о месте загрузки Steam из реестра"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            winreg.CloseKey(key)
            return steam_path.replace("/", "\\")
        except Exception as e:
            print(f"Ошибка получения места установки Steam из реестра: {e}")
            print("Попытка найти Steam в стандартных местах...")

            possible_paths = [
                os.path.expandvars(r"%ProgramFiles(x86)%\Steam"),
                os.path.expandvars(r"%ProgramFiles%\Steam"),
                r"C:\Program Files (x86)\Steam",
                r"C:\Program Files\Steam",
                os.path.join(os.path.expanduser("~"), "Steam")
            ]

            for path in possible_paths:
                if os.path.exists(os.path.join(path, "logs", "content_log.txt")):
                    print(f"Найден Steam по пути: {path}")
                    return path

            print("Steam не найден. Убедитесь, что Steam установлен.")
            sys.exit(1)

    def get_game_name_from_manifest(self, app_id):
        """Пытается получить название игры из манифеста"""
        try:
            steamapps_path = os.path.join(self.steam_path, 'steamapps')
            manifest_file = f'appmanifest_{app_id}.acf'
            manifest_path = os.path.join(steamapps_path, manifest_file)

            if os.path.exists(manifest_path):
                with open(manifest_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    # Ищем название в манифесте
                    name_match = re.search(r'"name"\s+"(.+?)"', content)
                    if name_match:
                        return name_match.group(1)
        except:
            pass

        # Если не нашли, возвращаем просто AppID
        return f"AppID: {app_id}"

    def parse_log_line(self, line):
        """Парсит строку лога для извлечения информации о загрузке"""
        try:
            line_lower = line.lower()

            # 1. Ищем текущую скорость загрузки (Mbps)
            speed_match = re.search(r'Current download rate:\s+([\d\.]+)\s+Mbps', line)
            if speed_match:
                speed_mbps = float(speed_match.group(1))
                self.download_speed = f"{speed_mbps:.2f} Mbps"
                self.last_update = datetime.now()

                # Если скорость 0, значит пауза
                if speed_mbps < 0.1:  # Меньше 0.1 Mbps считаем паузой
                    self.paused = True
                    self.status = "На паузе"
                else:
                    self.download_active = True
                    self.paused = False
                    self.status = "Загружается"

                # Конвертируем в MB/s для истории
                speed_mb_s = speed_mbps / 8
                self.speed_history.append(speed_mb_s)
                if len(self.speed_history) > 10:
                    self.speed_history.pop(0)

                return True

            # 2. Ищем начало активной загрузки
            # Строка типа: AppID 238960 state changed : Update Required,Update Queued,Update Running,Update Started,
            if "appid" in line_lower and "update started" in line_lower:
                match = re.search(r'appid (\d+)', line_lower)
                if match:
                    app_id = match.group(1)
                    self.current_app_id = app_id

                    # Получаем имя игры
                    game_name = self.get_game_name_from_manifest(app_id)
                    self.current_game = game_name

                    self.download_active = True
                    self.status = "Загружается"
                    return True

            # 3. Ищем информацию о размере загрузки для прогресса
            # Строка типа: AppID 238960 update started : download 0/58296299424
            if "update started" in line_lower and "download" in line_lower:
                # Ищем AppID
                app_match = re.search(r'appid (\d+)', line_lower)
                if app_match:
                    app_id = app_match.group(1)

                    # Если это текущая загрузка или мы еще не знаем, какая игра
                    if not self.current_app_id or self.current_app_id == app_id:
                        self.current_app_id = app_id

                    if self.current_game.startswith("AppID:"):
                        game_name = self.get_game_name_from_manifest(app_id)
                        self.current_game = game_name

                    return True

            # 4. Ищем паузу для текущей игры - ищем SUSPENDED
            if 'suspended' in line_lower:
                # Проверяем, есть ли в строке AppID
                app_match = re.search(r'appid (\d+)', line_lower)
                if app_match:
                    app_id = app_match.group(1)
                    if app_id == self.current_app_id:
                        self.paused = True
                        self.status = "На паузе"
                        self.download_speed = "0.0 Mbps"
                        return True

            # 5. Ищем возобновление для текущей игры
            if 'resumed' in line_lower and ('update' in line_lower or 'download' in line_lower):
                app_match = re.search(r'appid (\d+)', line_lower)
                if app_match:
                    app_id = app_match.group(1)
                    if app_id == self.current_app_id:
                        self.paused = False
                        self.status = "Загружается"
                        print(f"Загрузка возобновлена")
                        return True

            # 6. Ищем завершение загрузки для текущей игры
            if 'finished update' in line_lower and self.current_app_id:
                # Проверяем, упоминается ли текущая игра
                if self.current_app_id and str(self.current_app_id) in line:
                    self.download_active = False
                    self.paused = False
                    self.status = "Завершено"
                    self.download_speed = "0.0 Mbps"
                    return True

            # 7. Ищем состояние "Fully Installed"
            if 'fully installed' in line_lower and self.current_app_id:
                if self.current_app_id and str(self.current_app_id) in line:
                    self.download_active = False
                    self.paused = False
                    self.status = "Установлена"
                    self.download_speed = "0.0 Mbps"
                    return True

        except Exception as e:
            print(f"Ошибка при парсинге лога: {e}")

        return False

    def get_average_speed(self):
        """Возвращает среднюю скорость загрузки в MB/s"""
        if not self.speed_history or self.paused:
            return "0.0 MB/s"

        avg_speed = sum(self.speed_history) / len(self.speed_history)
        return f"{avg_speed:.2f} MB/s"

    def monitor_logs(self):
        """Мониторит логи Steam в реальном времени"""
        print(f"Мониторинг логов Steam в реальном времени из файла: {self.log_file}")

        # Ждем создания файла, если его нет
        while not os.path.exists(self.log_file):
            print("Файл логов не найден. Ожидание...")
            time.sleep(2)

        print("Файл найден. Ожидание данных о загрузках...")

        while self.running:
            try:
                with open(self.log_file, 'r', encoding='UTF-8', errors='ignore') as f:
                    f.seek(0, 2)
                    current_size = f.tell()

                    # Если файл уменьшился (перезаписан), начинаем сначала
                    if current_size < self.last_position:
                        self.last_position = 0

                    f.seek(self.last_position)

                    new_lines = f.read().splitlines()

                    for line in new_lines:
                        if line.strip():
                            self.parse_log_line(line)

                    self.last_position = f.tell()

                # Если есть активная загрузка, но скорость 0 больше 10 секунд - это пауза
                if self.download_active and not self.paused:
                    time_since_update = (datetime.now() - self.last_update).seconds
                    if time_since_update > 10 and self.download_speed == "0.00 Mbps":
                        self.paused = True
                        self.status = "На паузе"

                time.sleep(2)

            except PermissionError:
                print("Ошибка доступа к файлу логов.")
                time.sleep(10)
            except FileNotFoundError:
                print(f"Файл логов не найден: {self.log_file}")
                time.sleep(10)
            except Exception as e:
                print(f"Ошибка чтения лога: {e}")
                time.sleep(10)

    def print_download_info(self):
        """Выводит информацию о загрузке каждую минуту в течение 5 минут"""
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=5)

        while datetime.now() < end_time and self.running:
            try:
                current_time = datetime.now().strftime("%H:%M:%S")
                time_remaining = (end_time - datetime.now()).seconds // 60

                os.system('cls' if os.name == 'nt' else 'clear')
                print("\n" + "-" * 60)
                print(f"STEAM SPEED DOWNLOAD MONITOR - {current_time}")
                print(f"Осталось времени: {time_remaining + 1} мин")
                print("-" * 60)

                if self.download_active:
                    print(f"ИГРА:           {self.current_game}")
                    print(f"APP ID:         {self.current_app_id or 'Не определен'}")
                    print(f"СТАТУС:         {self.status}")
                    print(f"СКОРОСТЬ:       {self.download_speed}")
                    print(f"СРЕДНЯЯ СКОРОСТЬ: {self.get_average_speed()}")
                    print(f"ОБНОВЛЕНО:      {self.last_update.strftime('%H:%M:%S')}")
                else:
                    print("ОЖИДАНИЕ АКТИВНОЙ ЗАГРУЗКИ")
                    print("\nДля отображения данных:")
                    print("1. Убедитесь, что Steam запущен")
                    print("2. Начните загрузку или обновление игры")
                    print("3. Скрипт автоматически определит активную загрузку")

                print("=" * 60)

                # Ждем 1 минуту (60 секунд)
                for i in range(60):
                    if not self.running:
                        break
                    time.sleep(1)

            except KeyboardInterrupt:
                print("\nМониторинг прерван пользователем")
                self.running = False
                break
            except Exception as e:
                print(f"Ошибка при выводе информации: {e}")
                time.sleep(5)

    def start(self):
        print(f"Путь к Steam: {self.steam_path}")

        log_thread = threading.Thread(target=self.monitor_logs, daemon=True)
        log_thread.start()

        time.sleep(2)

        self.print_download_info()

        self.running = False

        print("\nМониторинг завершен")


if __name__ == '__main__':
    monitor = SteamSpeedDownloadMonitor()
    monitor.start()
