# трансляция экрана по UDP A.B.Glazov 14/08/2026
# verd  2.0.5

import sys
import subprocess
import importlib

# ============================================================================
# Проверка и автоматическая установка отсутствующих сторонних библиотек.
# Выполняется ДО импорта самих библиотек, чтобы программа могла запуститься
# "с чистого листа" (например, на новой машине) без ручной предварительной
# установки зависимостей. Тест
# ============================================================================

# соответствие "имя модуля для import" -> "имя пакета для pip install"
REQUIRED_PACKAGES = {
    "PIL": "Pillow",
    "psutil": "psutil",
}
if sys.platform.startswith("win"):
    # dxcam использует DXGI Desktop Duplication API и существует только
    # для Windows -- на других ОС пытаться его ставить бессмысленно
    REQUIRED_PACKAGES["dxcam"] = "dxcam"


def ensure_dependencies():
    """
    Проверяет импортируемость каждой библиотеки из REQUIRED_PACKAGES и
    автоматически устанавливает через pip те, которых не хватает.
    Ошибка установки одной библиотеки не прерывает установку остальных --
    сама программа при недоступности необязательных библиотек (psutil,
    dxcam) переходит на более простые запасные способы работы (см.
    get_local_ip / grab_screen), поэтому здесь можно только предупредить
    и продолжить.
    """
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    print(f"Отсутствуют библиотеки: {', '.join(missing)}. Устанавливаю через pip...")
    for pip_name in missing:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", pip_name],
                check=True,
            )
            print(f"  {pip_name}: установлен успешно")
        except Exception as e:
            print(f"  {pip_name}: не удалось установить автоматически ({e})")
            print(f"  Установите вручную: {sys.executable} -m pip install {pip_name}")


ensure_dependencies()

# ============================================================================
# Импорт библиотек (после проверки/установки зависимостей выше)
# ============================================================================
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import socket
import struct
import threading
import time
from PIL import ImageGrab, Image, ImageTk, ImageChops
import io
import os
import hashlib

try:
    import dxcam
except ImportError:
    dxcam = None

# версия с отправкой файлов 2026/05/05
""" Блокируем повторный запуск """
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", 5009))
except OSError:
    print("Программа уже запущена!")
    sys.exit(0)

""" Создание папки files """
FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")
os.makedirs(FILES_DIR, exist_ok=True)

try:
    import psutil
except ImportError:
    psutil = None

# ============================================================================
# Захват экрана: dxcam (Desktop Duplication API, Windows) с автоматическим
# откатом на PIL.ImageGrab, если dxcam недоступен или не смог захватить кадр
# (не Windows, нет GPU-доступа, RDP-сессия без поддержки DDA и т.п.).
# dxcam заметно быстрее ImageGrab, особенно на больших областях и высоком
# целевом FPS, т.к. читает кадр напрямую из GPU через DXGI, а не через GDI.
# ============================================================================
_dxcam_camera = None
_dxcam_broken = False  # если dxcam упал один раз -- дальше не пытаемся снова


def _get_dxcam_camera():
    """Лениво создаёт единственный экземпляр dxcam-камеры на всё время работы программы."""
    global _dxcam_camera, _dxcam_broken
    if dxcam is None or _dxcam_broken:
        return None
    if _dxcam_camera is None:
        try:
            _dxcam_camera = dxcam.create(output_color="RGB")
            if _dxcam_camera is None:
                # dxcam.create() может вернуть None, если не нашёл подходящий
                # вывод/адаптер (например, работа через RDP)
                _dxcam_broken = True
                print("dxcam: не удалось создать камеру захвата, использую ImageGrab")
        except Exception as e:
            print(f"dxcam: ошибка инициализации, использую ImageGrab: {e}")
            _dxcam_broken = True
            _dxcam_camera = None
    return _dxcam_camera


def grab_screen(region=None):
    """
    Единая точка захвата экрана для всей программы.
    region: (x1, y1, x2, y2) или None (весь экран) -- тот же формат, что и
    PIL.ImageGrab.grab(bbox=...).
    Возвращает PIL.Image в режиме RGB.
    """
    global _dxcam_broken
    camera = _get_dxcam_camera()
    if camera is not None:
        try:
            frame = camera.grab(region=region)
            if frame is None:
                # кадр ещё не готов (Desktop Duplication иногда не успевает
                # к моменту вызова) -- одна короткая повторная попытка
                frame = camera.grab(region=region)
            if frame is not None:
                return Image.fromarray(frame)
        except Exception as e:
            print(f"dxcam: ошибка захвата кадра, переключение на ImageGrab: {e}")
            _dxcam_broken = True

    # запасной способ: обычный захват через GDI
    return ImageGrab.grab(bbox=region).convert('RGB')


# Ключевые слова в имени интерфейса, по которым отсеиваем виртуальные
# адаптеры и VPN -- Radmin VPN, VirtualBox/VMware, Hyper-V, туннели и т.п.
# Сравнение регистронезависимое, поэтому годится для русских и английских
# названий адаптеров.
ADAPTER_EXCLUDE_KEYWORDS = [
    'virtualbox', 'vbox', 'vmware', 'hyper-v', 'hyperv', 'vethernet',
    'radmin', 'vpn', 'tap-', 'tap ', 'tap9', 'tun', 'wireguard',
    'openvpn', 'zerotier', 'tailscale', 'nordvpn', 'proton', 'anydesk',
    'teamviewer', 'loopback', 'docker', 'wsl', 'ppp', 'teredo', 'isatap',
    'bluetooth', 'npcap',
]


def _adapter_looks_virtual(name):
    name_lower = name.lower()
    return any(kw in name_lower for kw in ADAPTER_EXCLUDE_KEYWORDS)


def list_candidate_adapters():
    """
    Возвращает список (имя_адаптера, ip) для интерфейсов, которые похожи
    на реальный Ethernet/Wi-Fi адаптер локальной сети: активны, имеют
    IPv4-адрес, не loopback и не совпадают с ключевыми словами VPN/
    виртуальных сетей (Radmin VPN, VirtualBox, VMware, Hyper-V и т.д.).
    Требует psutil; если его нет -- возвращает пустой список (сработает
    запасной способ в get_local_ip).
    """
    if psutil is None:
        return []

    candidates = []
    try:
        all_addrs = psutil.net_if_addrs()
        all_stats = psutil.net_if_stats()
    except Exception as e:
        print(f"Не удалось получить список сетевых адаптеров: {e}")
        return []

    for name, addrs in all_addrs.items():
        if _adapter_looks_virtual(name):
            continue

        stats = all_stats.get(name)
        if stats is not None and not stats.isup:
            continue

        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                candidates.append((name, addr.address))

    # реальные Ethernet/Wi-Fi адаптеры -- в начало списка
    def sort_key(item):
        n = item[0].lower()
        return 0 if ('ethernet' in n or 'локальная сеть' in n or 'wi-fi' in n or 'wlan' in n) else 1

    candidates.sort(key=sort_key)
    return candidates


""" Определяем свой IP адрес """
def get_local_ip():
    """
    Пытается выбрать IP реального Ethernet/Wi-Fi адаптера, исключая
    виртуальные сети и VPN (см. list_candidate_adapters). Если psutil
    не установлен или подходящих адаптеров не нашлось -- запасной способ:
    ОС сама выбирает исходящий интерфейс по таблице маршрутизации для
    UDP-сокета (connect() на UDP не отправляет пакетов в сеть). Он менее
    надёжен на многосетевых машинах, т.к. может выбрать VPN, если у того
    маршрут по умолчанию.
    """
    candidates = list_candidate_adapters()
    if candidates:
        return candidates[0][1]

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()

my_ipaddr = get_local_ip()
print(my_ipaddr)

""" Screen Broadcaster (с интерфейсом и перезапуском) """
class ScreenBroadcaster:
    def __init__(self):
        self.root = tk.Tk()


        self.root.title("UDP_ABG")
        # Перехватываем нажатие на крестик
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        #self.root.geometry("450x480")
        self.root.resizable(False, False)

        # состояние
        self.region = None
        self.broadcasting = False
        self.broadcast_thread = None

        # UDP настройки
        self.udp_port = 5006
        self.file_port = 5007  # порт для передачи файлов
        self.command_port = 5008   # новый порт для команд
        self.broadcast_ip = '255.255.255.255'
        self.sock = None  # создаётся при старте трансляции

        # сетевой адаптер, на котором должна работать программа:
        # по умолчанию -- авто-подобранный реальный Ethernet/Wi-Fi,
        # исключая Radmin VPN/VirtualBox/VMware/Hyper-V и прочие
        # виртуальные сети (см. get_local_ip / list_candidate_adapters)
        self.adapter_candidates = list_candidate_adapters()  # [(имя, ip), ...]
        self.local_ip = my_ipaddr

        # окно приёма
        self.receiver_window = None
        self.receiving = False

        # передача файлов
        self.file_sending = False
        self.file_receiving = False
        self.file_recv_thread = None

        # выполнение команд
        self.command_receiving = False
        self.command_recv_thread = None

        # признак полной функциональности
        self.transmitter_mode = False

        # ==== интерфейс ====
        tk.Label(self.root, text="Трансляция Экрана", font=("Arial", 14, "bold")).pack(pady=8)
        # tk.Label(self.root, text=f"IP: {self.broadcast_ip}    Порт: {self.udp_port}", font=("Arial", 10)).pack(pady=4)

        self.status = tk.Label(self.root, text=" Ожидание действий...", fg="gray")
        self.status.pack(pady=5)

        # === Выбор сетевого адаптера ===
        # Всегда видим (не скрывается в режиме приёмника), т.к. влияет и на
        # приём, и на передачу. Список формируется из реальных Ethernet/Wi-Fi
        # адаптеров, исключая Radmin VPN, VirtualBox, VMware, Hyper-V и
        # прочие виртуальные сети (см. list_candidate_adapters).
        frame_adapter = tk.LabelFrame(self.root, text="Сетевой адаптер", padx=10, pady=6)
        frame_adapter.pack(padx=10, pady=5, fill="x")

        self.adapter_var = tk.StringVar()
        self.adapter_combo = ttk.Combobox(
            frame_adapter, textvariable=self.adapter_var, state="readonly", width=30
        )
        self.adapter_combo.pack(side="left", padx=(0, 5))
        self.adapter_combo.bind("<<ComboboxSelected>>", self.on_adapter_selected)

        #tk.Button(frame_adapter, text="⟳", width=3, command=self.refresh_adapters).pack(side="left")

        self.refresh_adapters()

        # === Раздел передачи ===
        frame_broadcast = tk.LabelFrame(self.root, text="Передача экрана", padx=10, pady=10)
        frame_broadcast.pack(padx=10, pady=5, fill="x")

        tk.Button(frame_broadcast, text="Выбрать область", width=28, command=self.select_region).pack(pady=3)
        tk.Button(frame_broadcast, text="Начать трансляцию", width=28, command=self.start_broadcast).pack(pady=3)
        tk.Button(frame_broadcast, text="Остановить трансляцию", width=28, command=self.stop_broadcast_ui).pack(pady=3)
        
        # --- Новые кнопки для быстрого выбора областей ---
        frame_quick_select = tk.LabelFrame(frame_broadcast, text="Быстрый выбор", padx=5, pady=5)
        frame_quick_select.pack(pady=5, fill="x")
        
        tk.Button(frame_quick_select, text="Показать весь экран", width=28, 
                  command=self.select_full_screen).pack(pady=2)
        tk.Button(frame_quick_select, text="Показать левую половину", width=28, 
                  command=self.select_left_half).pack(pady=2)
        tk.Button(frame_quick_select, text="Показать правую половину", width=28, 
                  command=self.select_right_half).pack(pady=2)
        tk.Button(frame_quick_select, text="Завершить показ экрана", width=28, 
                  command=self.stop_broadcast_and_clear).pack(pady=2)

        # === Раздел приёма ===
        frame_receive = tk.LabelFrame(self.root, text="Приём трансляции", padx=10, pady=10)
        frame_receive.pack(padx=10, pady=5, fill="x")

        self.btn_startview = tk.Button(frame_receive, text="Подключиться к трансляции", width=28, command=self.start_receiving)
        self.btn_startview.pack(pady=3)

        self.btn_stopview = tk.Button(frame_receive, text="Отключиться от трансляции", width=28, command=self.stop_receiving)
        self.btn_stopview.pack(pady=3)

        # === Раздел передачи файлов ===
        frame_files = tk.LabelFrame(self.root, text="Передача файлов (UDP Broadcast)", padx=10, pady=10)
        frame_files.pack(padx=10, pady=5, fill="x")

        tk.Button(frame_files, text="Выбрать и отправить файл", width=28, command=self.send_file).pack(pady=3)

        self.file_status = tk.Label(frame_files, text="Ожидание файлов...", fg="gray", font=("Arial", 9))
        self.file_status.pack(pady=3)

        tk.Button(frame_files, text="Отправить команду", width=28, command=self.send_command).pack(pady=3)

        # --- отдельный фрейм для кнопок ---
        frame_cmd_buttons = tk.Frame(frame_files)
        frame_cmd_buttons.pack(pady=3)

        buttons = [
            ("R", "start_view", "Запустить просмотр на всех"),
            ("F", "full_size", "Задать полный размер окна "),
            ("S", "stop_view", "Остановить просмотр на всех "),
            ("X", "stop_trans", "Прекратить все трансляции"),
        ]

        for idx, (text, cmd, tip) in enumerate(buttons):
            btn_name='btn'+str(idx)
            btn_name = tk.Button(frame_cmd_buttons,
                    text=text, width=3, 
                    command=lambda c=cmd: self.send_command(c))
            btn_name.grid(row=0, column=idx, padx=3, pady=3)
            ToolTip(btn_name, tip)

        # автоматически запускаем приём файлов
        self.border_overlay = None
        self.start_file_receiving()

        # автоматически запускаем приём команд
        self.start_command_receiving()

        # Горячие клавиши для включения/выключения режима передатчика
        self.root.bind_all("<Control-t>", self.enable_transmitter_mode)
        self.root.bind_all("<Control-r>", self.disable_transmitter_mode)
        self.root.bind_all("<Control-u>", self.enable_quick_mode)

        #--2026/05/05---
        self.root.bind_all("<Control-f>", self.send_file)
        self.root.bind_all("<Control-i>", self.show_ip)
        self.root.bind_all("<Control-h>", self.show_help)
        self.root.bind_all("<Control-z>", self.quit_app)
        self.root.bind_all("<Control-minus>", self.clear_files_all)
        self.root.bind_all("<Control-n>", self.open_network_connections)
        self.root.bind_all("<Control-x>", self.shutdown_all)


        # Скрываем все, кроме приёма
        frame_broadcast.pack_forget()
        frame_files.pack_forget()
        tk.Label(self.root, text="").pack(pady=5)  # немного отступа

        self.frame_broadcast = frame_broadcast
        self.frame_receive = frame_receive
        self.frame_files = frame_files
        self.frame_quick_select = frame_quick_select


        tk.Label(self.root, text="Выделение: мышь", fg="gray", font=("Arial", 8)).pack(pady=5)

    # --- выбор сетевого адаптера ---
    def refresh_adapters(self):
        """Пересканировать сетевые адаптеры и обновить список в комбобоксе."""
        self.adapter_candidates = list_candidate_adapters()

        if not self.adapter_candidates:
            # psutil недоступен или подходящих адаптеров не нашлось --
            # остаёмся на текущем self.local_ip (авто-запасной способ)
            self.adapter_combo['values'] = [f"(авто) {self.local_ip}"]
            self.adapter_var.set(f"(авто) {self.local_ip}")
            return

        values = [f"{name}  ({ip})" for name, ip in self.adapter_candidates]
        self.adapter_combo['values'] = values

        # если текущий self.local_ip есть среди найденных -- оставляем выбор,
        # иначе берём первый (лучший по эвристике) кандидат
        current_idx = 0
        for idx, (name, ip) in enumerate(self.adapter_candidates):
            if ip == self.local_ip:
                current_idx = idx
                break
        else:
            self.local_ip = self.adapter_candidates[0][1]

        self.adapter_var.set(values[current_idx])

    def on_adapter_selected(self, event=None):
        idx = self.adapter_combo.current()
        if 0 <= idx < len(self.adapter_candidates):
            name, ip = self.adapter_candidates[idx]
            self.local_ip = ip
            self.status.config(text=f" Адаптер: {name} ({ip})", fg="blue")

            # приём файлов/команд работает в фоне постоянно -- перевязываем
            # сразу на новый адаптер, чтобы не ждать перезапуска программы
            self.stop_file_receiving()
            self.start_file_receiving()
            self.stop_command_receiving()
            self.start_command_receiving()

            if self.broadcasting or self.receiving:
                self.status.config(
                    text=" Адаптер изменён — перезапустите трансляцию/приём экрана, чтобы применить",
                    fg="orange",
                )

    # --- выбор области ---
    def select_region(self):
        overlay = tk.Toplevel()
        overlay.attributes('-fullscreen', True)
        overlay.attributes('-alpha', 0.3)
        overlay.configure(bg='gray')

        canvas = tk.Canvas(overlay, cursor="cross", bg='gray')
        canvas.pack(fill=tk.BOTH, expand=True)

        start_x = start_y = rect = None

        def on_press(event):
            nonlocal start_x, start_y, rect
            start_x, start_y = event.x, event.y
            if rect:
                canvas.delete(rect)
            rect = canvas.create_rectangle(start_x, start_y, start_x, start_y, outline='blue', width=3)
            #rect1 = canvas.create_rectangle(start_x-3, start_y-3, start_x+3, start_y+3, outline='white', width=3)

        def on_drag(event):
            canvas.coords(rect, start_x, start_y, event.x, event.y)

        def on_release(event):
            x1, y1 = min(start_x, event.x), min(start_y, event.y)
            x2, y2 = max(start_x, event.x), max(start_y, event.y)
            self.region = (x1, y1, x2, y2)
            self.status.config(text=f" Область выбрана: {self.region}", fg="green")
            overlay.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", lambda e: overlay.destroy())

    # --- Новые методы для быстрого выбора областей ---
    def select_full_screen(self):
        #self.stop_broadcast_and_clear()
        #self.region = None
        """Выбрать весь экран и автоматически запустить трансляцию"""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.region = (0, 0, screen_width, screen_height)
        self.status.config(text=f" Выбран весь экран: {screen_width}x{screen_height}", fg="green")
        
        # Запускаем автоматические действия с задержками через after
        self.root.after(500, self.start_broadcast)  # Задержка 0.5 сек, затем начать трансляцию
        self.root.after(1000, lambda: self.send_command("start_view"))  # Задержка 1 сек, затем кнопка R
        self.root.after(1500, lambda: self.send_command("full_size"))  # Задержка 1.5 сек, затем кнопка F

    def select_left_half(self):
        #self.stop_broadcast_and_clear()
        #self.region = None
        """Выбрать левую половину экрана"""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.region = (0, 0, screen_width // 2, screen_height)
        self.status.config(text=f" Выбрана левая половина экрана", fg="green")

        self.root.after(500, self.start_broadcast)  # Задержка 0.5 сек, затем начать трансляцию
        self.root.after(1000, lambda: self.send_command("start_view"))  # Задержка 1 сек, затем кнопка R
        self.root.after(1500, lambda: self.send_command("full_size"))  # Задержка 1.5 сек, затем кнопка F

    def select_right_half(self):
        #self.stop_broadcast_and_clear()
        #self.region = None
        """Выбрать правую половину экрана"""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.region = (screen_width // 2, 0, screen_width, screen_height)
        self.status.config(text=f" Выбрана правая половина экрана", fg="green")

        self.root.after(500, self.start_broadcast)  # Задержка 0.5 сек, затем начать трансляцию
        self.root.after(1000, lambda: self.send_command("start_view"))  # Задержка 1 сек, затем кнопка R
        self.root.after(1500, lambda: self.send_command("full_size"))  # Задержка 1.5 сек, затем кнопка F

    def stop_broadcast_and_clear(self):
        """Остановить трансляцию и очистить выбранную область"""
        self.stop_broadcast_ui()
        
        # Задержка 0.5 сек, затем отправить команду stop_view (кнопка S)
        self.root.after(500, lambda: self.send_command("stop_view"))
        
        # Очищаем область после выполнения команд
        self.root.after(1000, self._clear_region)
    
    def _clear_region(self):
        """Очистка выбранной области"""
        self.region = None
        self.status.config(text=" Трансляция завершена, область очищена", fg="gray")

    # --- старт трансляции ---
    def start_broadcast(self):
        if self.broadcasting:
            self.status.config(text=" Трансляция уже идёт!", fg="red")
            return
        if not self.region:
            self.status.config(text=" Сначала выберите область!", fg="red")
            return

        # пересоздаём сокет при каждом запуске
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # привязываем к конкретному адаптеру, чтобы broadcast всегда
            # уходил именно через него, а не через VPN/виртуальную сеть,
            # которая может оказаться маршрутом по умолчанию
            self.sock.bind((self.local_ip, 0))
        except OSError as e:
            self.status.config(text=f" Не удалось привязаться к {self.local_ip}: {e}", fg="red")
            self.sock.close()
            self.sock = None
            return

        self.status.config(text=" Трансляция запущена...", fg="green")
        self.broadcasting = True
        self.broadcast_thread = threading.Thread(target=self.broadcast_screen, daemon=True)
        self.broadcast_thread.start()
        self.show_region_border()

    # --- вычисление bounding-box изменившейся области ---
    def _diff_bbox(self, prev_img, cur_img, scale=4, threshold=15):
        """
        Быстро находит прямоугольник, охватывающий изменившиеся пиксели.
        Сравнение делается на уменьшенной копии (scale) для скорости,
        затем результат масштабируется обратно с небольшим запасом (pad),
        чтобы не потерять мелкие детали на границах.
        Возвращает (x1, y1, x2, y2) или None, если изменений нет.
        """
        w, h = cur_img.size
        sw, sh = max(1, w // scale), max(1, h // scale)
        small_prev = prev_img.resize((sw, sh))
        small_cur = cur_img.resize((sw, sh))

        diff = ImageChops.difference(small_prev, small_cur).convert('L')
        bbox = diff.point(lambda p: 255 if p > threshold else 0).getbbox()
        if bbox is None:
            return None

        x1, y1, x2, y2 = bbox
        pad = scale * 2
        x1 = max(0, x1 * scale - pad)
        y1 = max(0, y1 * scale - pad)
        x2 = min(w, x2 * scale + pad)
        y2 = min(h, y2 * scale + pad)
        return (x1, y1, x2, y2)

    # --- нарезка payload на чанки и отправка ---
    def _send_frame_payload(self, payload, frame_id, chunk_size):
        total_chunks = (len(payload) + chunk_size - 1) // chunk_size
        for i in range(total_chunks):
            if not self.broadcasting:
                break
            start = i * chunk_size
            end = min(start + chunk_size, len(payload))
            chunk = payload[start:end]
            header = struct.pack('!IIII', frame_id, i, total_chunks, len(chunk))
            self.sock.sendto(header + chunk, (self.broadcast_ip, self.udp_port))

    # --- поток трансляции ---
    def broadcast_screen(self):
        frame_id = 0

        # Оптимизация сети/FPS (без смены протокола UDP):
        #  1) chunk_size укладывается в Ethernet MTU (1500 байт), чтобы избежать
        #     IP-фрагментации: потеря одного UDP-пакета ~= потеря маленького
        #     кусочка кадра, а не всего кадра целиком.
        #  2) Keyframe раз в секунду + delta-кадры (только изменившийся
        #     прямоугольник) между ними -- экономит трафик на типичной
        #     работе (курсор, набор текста, скролл).
        #  3) Если ничего не изменилось -- кадр вообще не отправляется.
        #  4) Если изменилось слишком много площади -- шлём keyframe вместо
        #     delta (лучше сжимается и не даёт "сыпаться" множеству мелких
        #     JPEG-кусков при смене сцены целиком).
        #  5) Целевой FPS выдерживается с учётом реального времени захвата,
        #     сравнения и кодирования кадра.
        chunk_size = 1400
        target_fps = 15
        target_interval = 1.0 / target_fps

        FRAME_TYPE_FULL = 0
        FRAME_TYPE_DELTA = 1

        quality_full = 75
        quality_delta = 80
        keyframe_interval = 1.0
        large_change_ratio = 0.5  # доля площади, при которой выгоднее full-кадр

        prev_frame = None          # предыдущий "сырой" кадр (для diff)
        last_keyframe_time = 0.0

        try:
            while self.broadcasting:
                t0 = time.perf_counter()

                screenshot = grab_screen(self.region)
                w, h = screenshot.size

                need_keyframe = (
                    prev_frame is None
                    or (t0 - last_keyframe_time) >= keyframe_interval
                )

                bbox = None
                if not need_keyframe:
                    bbox = self._diff_bbox(prev_frame, screenshot)
                    if bbox is not None:
                        bx1, by1, bx2, by2 = bbox
                        area_ratio = ((bx2 - bx1) * (by2 - by1)) / max(1, w * h)
                        if area_ratio > large_change_ratio:
                            need_keyframe = True

                if need_keyframe:
                    img_buffer = io.BytesIO()
                    screenshot.save(img_buffer, format='JPEG', quality=quality_full)
                    jpeg_bytes = img_buffer.getvalue()
                    payload = struct.pack('!B', FRAME_TYPE_FULL) + jpeg_bytes

                    self._send_frame_payload(payload, frame_id, chunk_size)
                    frame_id += 1
                    last_keyframe_time = t0
                    prev_frame = screenshot

                elif bbox is not None:
                    x1, y1, x2, y2 = bbox
                    region_img = screenshot.crop((x1, y1, x2, y2))
                    img_buffer = io.BytesIO()
                    region_img.save(img_buffer, format='JPEG', quality=quality_delta)
                    jpeg_bytes = img_buffer.getvalue()

                    header = struct.pack('!BHHHH', FRAME_TYPE_DELTA, x1, y1, x2 - x1, y2 - y1)
                    payload = header + jpeg_bytes

                    self._send_frame_payload(payload, frame_id, chunk_size)
                    frame_id += 1
                    prev_frame = screenshot

                # если bbox is None -- ничего не изменилось, кадр не отправляем

                elapsed = time.perf_counter() - t0
                time.sleep(max(0, target_interval - elapsed))
        except Exception as e:
            self.status.config(text=f" Ошибка передачи: {e}", fg="red")
            self.broadcasting = False

    # --- остановка передачи через UI ---
    def stop_broadcast_ui(self):
        if self.broadcasting:
            self.stop_broadcast()
            self.status.config(text=" Трансляция остановлена", fg="gray")
        else:
            self.status.config(text=" Трансляция не запущена", fg="red")

    def stop_broadcast(self):
        if self.border_overlay and self.border_overlay.winfo_exists():
            self.border_overlay.destroy()
            self.border_overlay = None
         
        if not self.broadcasting:
            return
        self.broadcasting = False
        try:
            if self.broadcast_thread and self.broadcast_thread.is_alive():
                self.broadcast_thread.join(timeout=1)
        except Exception:
            pass
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

    # --- старт приёма ---
    def start_receiving(self):
        if self.receiving:
            self.status.config(text=" Уже подключены к трансляции!", fg="red")
            return

        if self.receiver_window and self.receiver_window.winfo_exists():
            self.receiver_window.lift()
            return

        self.receiving = True
        self.receiver_window = tk.Toplevel(self.root)
        self.receiver_window.title("Просмотр трансляции")
        self.receiver_window.attributes('-topmost', True)
        self.receiver_window.geometry("800x600")
        
        self.receiver_label = tk.Label(self.receiver_window, text="Ожидание трансляции...", bg="black", fg="white")
        self.receiver_label.pack(fill=tk.BOTH, expand=True)

        # создаём сокет для приёма
        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # биндимся на IP конкретного адаптера (не на '' / 0.0.0.0), чтобы
            # принимать трансляцию только с выбранной локальной сети,
            # исключая Radmin VPN / VirtualBox / VMware / Hyper-V и т.п.
            self.recv_sock.bind((self.local_ip, self.udp_port))
        except OSError as e:
            self.status.config(text=f" Не удалось привязаться к {self.local_ip}: {e}", fg="red")
            self.recv_sock.close()
            self.recv_sock = None
            self.receiving = False
            self.receiver_window.destroy()
            self.receiver_window = None
            return
        self.recv_sock.settimeout(0.1)  # таймаут для проверки флага

        self.frames = {}
        self.frame_times = {}  # frame_id -> время первого чанка (для очистки "зависших" кадров)
        self.canvas_image = None  # накопленный кадр: keyframe заменяет целиком, delta -- вставляется поверх
        self.receive_thread = threading.Thread(target=self.receive_screen, daemon=True)
        self.receive_thread.start()

        self.status.config(text=" Подключено к приёму трансляции", fg="blue")

        # обработка закрытия окна
        self.receiver_window.protocol("WM_DELETE_WINDOW", self.stop_receiving)
        self.receiver_window.bind("<Escape>", lambda e: self.stop_receiving())

    # --- поток приёма ---
    def receive_screen(self):
        while self.receiving:
            try:
                data, addr = self.recv_sock.recvfrom(65535)

                # блокируем приём собственной трансляции: при broadcast ОС
                # доставляет пакеты и отправителю тоже, если он слушает этот
                # порт -- фильтруем по IP отправителя.
                if addr[0] == self.local_ip:
                    continue

                header = struct.unpack('!IIII', data[:16])
                frame_id, chunk_idx, total_chunks, _ = header
                chunk_data = data[16:]

                now = time.time()
                frame = self.frames.setdefault(frame_id, {})
                if frame_id not in self.frame_times:
                    self.frame_times[frame_id] = now
                frame[chunk_idx] = chunk_data

                if len(frame) == total_chunks:
                    img_data = b''.join(frame[i] for i in range(total_chunks))
                    self.frames.pop(frame_id, None)
                    self.frame_times.pop(frame_id, None)

                    # удаляем старые/зависшие незавершённые фреймы:
                    # по количеству (не больше 10 в буфере)
                    if len(self.frames) > 10:
                        oldest = min(self.frames.keys())
                        self.frames.pop(oldest, None)
                        self.frame_times.pop(oldest, None)

                    # и по возрасту (потерянный чанк -> кадр никогда не соберётся)
                    stale_ids = [fid for fid, t in self.frame_times.items() if now - t > 1.0]
                    for fid in stale_ids:
                        self.frames.pop(fid, None)
                        self.frame_times.pop(fid, None)

                    try:
                        frame_type = img_data[0]

                        if frame_type == 0:
                            # keyframe -- полный кадр, заменяет холст целиком
                            image = Image.open(io.BytesIO(img_data[1:])).convert('RGB')
                            self.canvas_image = image
                        else:
                            # delta -- только изменившийся прямоугольник
                            x1, y1, w, h = struct.unpack('!HHHH', img_data[1:9])
                            region = Image.open(io.BytesIO(img_data[9:])).convert('RGB')

                            if self.canvas_image is None:
                                # ещё не было ни одного keyframe -- ждём его,
                                # без полного кадра вставлять delta некуда
                                self.canvas_image = None
                            else:
                                self.canvas_image.paste(region, (x1, y1))

                        if self.canvas_image is not None:
                            tk_image = ImageTk.PhotoImage(self.canvas_image)

                            if self.receiving and self.receiver_window.winfo_exists():
                                self.receiver_label.config(image=tk_image, text="")
                                self.receiver_label.image = tk_image
                    except Exception as e:
                        print(f"Ошибка отображения: {e}")

            except socket.timeout:
                continue
            except Exception as e:
                if self.receiving:
                    print(f"Ошибка приёма: {e}")
                time.sleep(0.05)

    # --- остановка приёма ---
    def stop_receiving(self):
        if not self.receiving:
            self.status.config(text=" Приём не был запущен", fg="red")
            return

        self.receiving = False
        
        try:
            if self.receive_thread and self.receive_thread.is_alive():
                self.receive_thread.join(timeout=1)
        except Exception:
            pass

        if self.recv_sock:
            try:
                self.recv_sock.close()
            except:
                pass
            self.recv_sock = None

        if self.receiver_window and self.receiver_window.winfo_exists():
            self.receiver_window.destroy()
        
        self.receiver_window = None
        self.canvas_image = None
        self.status.config(text=" Отключено от трансляции", fg="gray")

    # --- отправка файла ---
    def send_file(self, key = 0):
        if self.file_sending:
            self.file_status.config(text=" Файл уже отправляется!", fg="red")
            return

        # выбор файла из папки files
        file_path = filedialog.askopenfilename(
            initialdir=FILES_DIR,
            title="Выберите файл для отправки",
            filetypes=[("Все файлы", "*.*")]
        )
        
        if not file_path:
            return

        threading.Thread(target=self._send_file_thread, args=(file_path,), daemon=True).start()

    def _send_file_thread(self, file_path):
        self.file_sending = True
        filename = os.path.basename(file_path)
        
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            file_size = len(file_data)
            file_hash = hashlib.md5(file_data).hexdigest()
            
            self.file_status.config(text=f" Отправка файла:\n {filename} ({file_size} байт)", fg="blue")
            
            # создаём сокет для отправки
            file_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            file_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            file_sock.bind((self.local_ip, 0))
            
            # отправляем метаданные
            filename_encoded = filename.encode('utf-8')
            meta_header = struct.pack('!I', len(filename_encoded)) + filename_encoded
            meta_header += struct.pack('!Q', file_size)  # размер файла
            meta_header += file_hash.encode('utf-8')  # MD5 хеш
            
            chunk_size = 60000
            total_chunks = (file_size + chunk_size - 1) // chunk_size
            
            # отправляем START сигнал с метаданными
            start_packet = b'FILE_START' + meta_header + struct.pack('!I', total_chunks)
            for _ in range(3):  # отправляем 3 раза для надёжности
                file_sock.sendto(start_packet, (self.broadcast_ip, self.file_port))
                time.sleep(0.05)
            
            # отправляем чанки
            for i in range(total_chunks):
                start = i * chunk_size
                end = min(start + chunk_size, file_size)
                chunk = file_data[start:end]
                
                header = struct.pack('!II', i, total_chunks)
                packet = b'FILE_CHUNK' + header + chunk
                file_sock.sendto(packet, (self.broadcast_ip, self.file_port))
                
                progress = int((i + 1) / total_chunks * 100)
                self.file_status.config(text=f" Отправка:\n {filename} [{progress}%]", fg="blue")
                time.sleep(0.01)  # небольшая задержка
            
            # отправляем END сигнал
            end_packet = b'FILE_END' + filename_encoded
            for _ in range(3):
                file_sock.sendto(end_packet, (self.broadcast_ip, self.file_port))
                time.sleep(0.05)
            
            file_sock.close()
            self.file_status.config(text=f" Отправлен файл:\n {filename}", fg="green")
            
        except Exception as e:
            self.file_status.config(text=f" Ошибка отправки: {e}", fg="red")
        finally:
            self.file_sending = False

    # --- приём файлов ---
    def start_file_receiving(self):
        if self.file_receiving:
            return
        
        self.file_receiving = True
        self.file_recv_thread = threading.Thread(target=self._receive_files_thread, daemon=True)
        self.file_recv_thread.start()

    def _receive_files_thread(self):
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            recv_sock.bind((self.local_ip, self.file_port))
        except OSError as e:
            print(f"Не удалось привязать приём файлов к {self.local_ip}: {e}")
            self.file_receiving = False
            recv_sock.close()
            return
        recv_sock.settimeout(0.5)
        
        current_file = None
        file_chunks = {}
        
        while self.file_receiving:
            try:
                data, addr = recv_sock.recvfrom(65535)
                
                if data.startswith(b'FILE_START'):
                    # получаем метаданные
                    offset = 10  # len('FILE_START')
                    filename_len = struct.unpack('!I', data[offset:offset+4])[0]
                    offset += 4
                    filename = data[offset:offset+filename_len].decode('utf-8')
                    offset += filename_len
                    file_size = struct.unpack('!Q', data[offset:offset+8])[0]
                    offset += 8
                    file_hash = data[offset:offset+32].decode('utf-8')
                    offset += 32
                    total_chunks = struct.unpack('!I', data[offset:offset+4])[0]
                    
                    current_file = {
                        'name': filename,
                        'size': file_size,
                        'hash': file_hash,
                        'total_chunks': total_chunks,
                        'chunks': {}
                    }
                    file_chunks = {}
                    self.file_status.config(text=f" Получение: {filename}", fg="blue")
                
                elif data.startswith(b'FILE_CHUNK') and current_file:
                    offset = 10
                    chunk_idx, total_chunks = struct.unpack('!II', data[offset:offset+8])
                    offset += 8
                    chunk_data = data[offset:]
                    
                    file_chunks[chunk_idx] = chunk_data
                    
                    progress = int(len(file_chunks) / current_file['total_chunks'] * 100)
                    self.file_status.config(text=f" Получение: {current_file['name']} [{progress}%]", fg="blue")
                    
                    # проверяем, все ли чанки получены
                    if len(file_chunks) == current_file['total_chunks']:
                        # собираем файл
                        file_data = b''.join(file_chunks[i] for i in range(current_file['total_chunks']))
                        
                        # проверяем хеш
                        received_hash = hashlib.md5(file_data).hexdigest()
                        if received_hash == current_file['hash']:
                            # сохраняем файл
                            save_path = os.path.join(FILES_DIR, current_file['name'])
                            
                            # если файл существует, добавляем номер
                            base, ext = os.path.splitext(current_file['name'])
                            counter = 1
                            while os.path.exists(save_path):
                                save_path = os.path.join(FILES_DIR, f"{base}_{counter}{ext}")
                                counter += 1
                            
                            with open(save_path, 'wb') as f:
                                f.write(file_data)
                            
                            self.file_status.config(text=f" Получено: {os.path.basename(save_path)}", fg="green")
                        else:
                            self.file_status.config(text=f" Ошибка контрольной суммы", fg="red")
                        
                        current_file = None
                        file_chunks = {}
                
                elif data.startswith(b'FILE_END'):
                    pass  # можно добавить дополнительную обработку
                    
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Ошибка приёма файла: {e}")
                time.sleep(0.1)
        
        recv_sock.close()

    def stop_file_receiving(self):
        self.file_receiving = False
        if self.file_recv_thread and self.file_recv_thread.is_alive():
            self.file_recv_thread.join(timeout=1)

    def run(self):
        #self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self.stop_broadcast()
        self.stop_receiving()
        self.stop_file_receiving()
        print("закрываюсь")
        #self.root.destroy()
        # Вместо закрытия — сворачиваем окно на панель задач
        self.root.iconify()

        
    def show_region_border(self):
        """Показ подсветки выбранной области поверх всех окон."""
        if not self.region:
            return

        x1, y1, x2, y2 = self.region

        # Если уже есть рамка — удаляем
        if self.border_overlay and self.border_overlay.winfo_exists():
            self.border_overlay.destroy()

        self.border_overlay = tk.Toplevel()
        self.border_overlay.overrideredirect(True)
        #self.border_overlay.attributes("-topmost", True)
        self.border_overlay.attributes("-transparentcolor", "white")
        self.border_overlay.geometry(f"{x2-x1}x{y2-y1}+{x1}+{y1}")

        canvas = tk.Canvas(self.border_overlay, bg="white", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # рисуем рамку
        canvas.create_rectangle(0, 0, x2-x1, y2-y1, outline="blue", width=4)

    def enable_transmitter_mode(self, event=None):
        """Активирует режим передатчика (Ctrl+T)."""
        if self.transmitter_mode:
            return  # уже активен

        self.transmitter_mode = True
        self.status.config(text=" Режим передатчика активирован", fg="green")

        # скрываем раздел приёма
        self.frame_receive.pack_forget()
        
        # показываем разделы передачи
        self.frame_broadcast.pack(padx=10, pady=5, fill="x")
        self.frame_files.pack(padx=10, pady=5, fill="x")
        
    def disable_transmitter_mode(self, event=None):        
        """Отключает режим передатчика (Ctrl+R) и скрывает блоки передачи."""
        if not self.transmitter_mode:
            return  # уже в режиме приёмника

        self.transmitter_mode = False
        self.status.config(text=" Режим передатчика отключён", fg="gray")

        # скрываем секции передачи
        self.frame_broadcast.pack_forget()
        self.frame_files.pack_forget()
        
        # показываем раздел приёма
        self.frame_receive.pack(padx=10, pady=5, fill="x")

    def enable_quick_mode(self, event=None):
        """Активирует быстрый режим передачи (Ctrl+U) - только панель быстрого выбора."""
        self.transmitter_mode = True
        self.status.config(text=" Быстрый режим передачи активирован", fg="green")

        # скрываем все разделы
        self.frame_broadcast.pack_forget()
        self.frame_receive.pack_forget()
        self.frame_files.pack_forget()
        
        # показываем только панель быстрого выбора
        # сначала показываем frame_broadcast, чтобы внутренний frame был виден
        self.frame_broadcast.pack(padx=10, pady=5, fill="x")
        
        # но скрываем все его виджеты кроме frame_quick_select
        for widget in self.frame_broadcast.winfo_children():
            if widget != self.frame_quick_select:
                widget.pack_forget()
        
        # убеждаемся что frame_quick_select виден
        self.frame_quick_select.pack(pady=5, fill="x")

    # --- отправка команды ---
    def send_command(self, command=None):
        """Запрос команды у пользователя и отправка на все приёмники."""
        if command is None:
            command = simpledialog.askstring("Отправка команды", 
                                            "Введите команду для выполнения на приёмниках:",
                                            parent=self.root)
            if command=='full_size':
                x1, y1, x2, y2 = self.region
                command = f"set_size {x2-x1}x{y2-y1}"
        elif command == "full_size":
            if self.region is None:
                self.file_status.config(text=" Сначала выберите область!", fg="red")
                return
            x1, y1, x2, y2 = self.region
            command = f"set_size {x2-x1}x{y2-y1}"


        if not command:
            return

        threading.Thread(target=self._send_command_thread, args=(command,), daemon=True).start()

    def _send_command_thread(self, command):
        """Поток отправки команды."""
        try:
            self.file_status.config(text=f" Отправка команды...", fg="blue")
            
            # создаём сокет для отправки
            cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            cmd_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            cmd_sock.bind((self.local_ip, 0))
            
            # кодируем команду
            command_encoded = command.encode('utf-8')
            packet = b'COMMAND_EXEC' + struct.pack('!I', len(command_encoded)) + command_encoded
            
            # отправляем 
            #for _ in range(3):
            cmd_sock.sendto(packet, (self.broadcast_ip, self.command_port))
            time.sleep(0.05)
            
            cmd_sock.close()
            self.file_status.config(text=f"Послана команда:\n {command[:30]}", fg="green")
            
        except Exception as e:
            self.file_status.config(text=f" Ошибка отправки команды: {e}", fg="red")

    # --- приём и выполнение команд ---
    def start_command_receiving(self):
        """Запуск потока приёма команд."""
        if self.command_receiving:
            return
        
        self.command_receiving = True
        self.command_recv_thread = threading.Thread(target=self._receive_commands_thread, daemon=True)
        self.command_recv_thread.start()

    def _receive_commands_thread(self):
        """Поток приёма и выполнения команд."""
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            recv_sock.bind((self.local_ip, self.command_port))
        except OSError as e:
            print(f"Не удалось привязать приём команд к {self.local_ip}: {e}")
            self.command_receiving = False
            recv_sock.close()
            return
        recv_sock.settimeout(0.5)
        
        print(f"Слушаю команды на порту {self.command_port}...")
        
        while self.command_receiving:
            try:
                data, addr = recv_sock.recvfrom(65535)
                # Блокируем свою команду себе
                if addr[0] == self.local_ip:
                    continue
                
                if data.startswith(b'COMMAND_EXEC'):
                    offset = 12  # len('COMMAND_EXEC')
                    cmd_len = struct.unpack('!I', data[offset:offset+4])[0]
                    offset += 4
                    command = data[offset:offset+cmd_len].decode('utf-8')
                    
                    print(f"Получена команда от {addr}: {command}")
                    
                    # выполняем команду в отдельном потоке
                    threading.Thread(target=self._execute_command, args=(command,), daemon=True).start()
                    
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Ошибка приёма команды: {e}")
                time.sleep(0.1)
        
        recv_sock.close()

    def _execute_command(self, command):
        """Выполнение команды через subprocess."""
        try:
            print(f"Выполнение команды: {command}")

            #предопределенные команды
            self.dc_cmd = {"set_size":self.set_size,
                           "start_view":self.start_view,
                           "stop_view":self.stop_view,
                           "stop_trans":self.stop_stream,
                           "clear_files":self.clear_files_local,
                           }
            ls_cmd = command.split(" ",1)
             
            if ls_cmd[0] in self.dc_cmd:
                loc_cmd = self.dc_cmd[ls_cmd[0]]
                if len(ls_cmd)==1:  # без параметров 
                    loc_cmd()
                else:              # с параметрами 
                    loc_cmd(ls_cmd[1] )
            
                return
            
            
            # выполняем команду
            result = subprocess.run(
                command,
                shell=True,
                #capture_output=True,
                #text=True,
                timeout=30  # таймаут 30 секунд
            )
            
            print(f"Команда завершена. Код возврата: {result.returncode}")
            if result.stdout:
                print(f"STDOUT: {result.stdout}")
            if result.stderr:
                print(f"STDERR: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            print(f"Команда превысила таймаут: {command}")
        except Exception as e:
            print(f"Ошибка выполнения команды: {e}")

    #--Список внутренних команд ----------
            
    def set_size(self, params):
        """ изменить размеры окна трансляции """
        if self.receiver_window:
            self.receiver_window.geometry( params )

    def start_view(self):
        """ запустить поток приема трансляции """
        self.btn_startview.invoke()

    def stop_view(self):
        """ остановить поток приема трансляции """
        self.btn_stopview.invoke()
        
    def stop_stream(self):
        """ остановить поток трансляции """
        self.stop_broadcast_ui()
        self.btn_stopview.invoke()

    #-------------------------------------    
 

    # 2026/05/05
 
    def clear_files_local(self):
        """Очистить папку files на этом компьютере (по команде из сети)."""
        for f in os.listdir(FILES_DIR):
            fp = os.path.join(FILES_DIR, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except Exception as e:
                print(f"Ошибка удаления {fp}: {e}")
        print("Папка files очищена по команде из сети")
 
    def show_ip(self, event=None):
        """Ctrl+I — показать свой IP адрес."""
        messagebox.showinfo("IP адрес", f"Мой IP адрес:\n{my_ipaddr}")

    def show_help(self, event=None):
        """Ctrl+H — справка о программе."""
        help_text = (
            "UDP_ABG — Трансляция экрана по локальной сети\n"
            "Версия 1.4.0\n\n"
            "Горячие клавиши:\n"
            "  Ctrl+T  — режим передатчика (показ всех панелей)\n"
            "  Ctrl+R  — режим приёмника (скрыть панели передачи)\n"
            "  Ctrl+U  — быстрый режим передачи\n"
            "  Ctrl+F  — выбрать и отправить файл\n"
            "  Ctrl+I  — показать свой IP адрес\n"
            "  Ctrl+H  — эта справка\n"
            "  Ctrl+Z  — полностью завершить программу\n"
            "  Ctrl+-  — очистить папку files на всех ПК сети\n"
            "  Ctrl+N  — открыть сетевые подключения (ncpa.cpl)\n"
            "  Ctrl+X  — выключить все компьютеры в сети\n\n"            
            "Кнопки команд:\n"
            "  R — запустить просмотр на всех\n"
            "  F — задать полный размер окна\n"
            "  S — остановить просмотр на всех\n"
            "  X — прекратить все трансляции\n\n"
            "Папка files находится рядом с программой."
        )
        messagebox.showinfo("Справка", help_text)

    def quit_app(self, event=None):
        """Ctrl+Z — полное завершение программы."""
        self.stop_broadcast()
        self.stop_receiving()
        self.stop_file_receiving()
        self.stop_command_receiving()
        self.root.destroy()
        sys.exit(0)

    def clear_files_all(self, event=None):
        """Ctrl+- — очистить папку files локально и на всех ПК сети."""
        # очищаем локально
        for f in os.listdir(FILES_DIR):
            fp = os.path.join(FILES_DIR, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except Exception as e:
                print(f"Ошибка удаления {fp}: {e}")
        self.file_status.config(text=" Папка files очищена локально", fg="gray")

        # отправляем команду на все ПК сети
        threading.Thread(
            target=self._send_command_thread,
            args=("clear_files",),
            daemon=True
        ).start()

    def open_network_connections(self, event=None):
        """Ctrl+N — открыть оснастку сетевых подключений."""
        subprocess.Popen("ncpa.cpl", shell=True)

    def shutdown_all(self, event=None):
        """Ctrl+X — отправить команду выключения всем ПК сети."""
        if not messagebox.askyesno(
            "Выключение компьютеров",
            "Отправить команду выключения ВСЕМ компьютерам в сети?\n\nЭто действие необратимо!"
        ):
            return
        threading.Thread(
            target=self._send_command_thread,
            args=("shutdown /p",),
            daemon=True
        ).start()


    #-------------------------------------    
    def stop_command_receiving(self):
        """Остановка приёма команд."""
        self.command_receiving = False
        if self.command_recv_thread and self.command_recv_thread.is_alive():
            self.command_recv_thread.join(timeout=1)

    def run(self):
        # Сворачивание окна после создания
        self.root.state(newstate='iconic')

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self.stop_broadcast()
        self.stop_receiving()
        self.stop_file_receiving()
        self.stop_command_receiving()
        self.root.iconify()


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x, y, _, _ = self.widget.bbox("insert") if self.widget.bbox("insert") else (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 20
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)  # убираем рамку
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=self.text, justify='left',
            background="#ffffe0", relief='solid', borderwidth=1,
            font=("tahoma", "8", "normal")
        )
        label.pack(ipadx=3, ipady=1)

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None



# =========================================================
# === Точка входа
# =========================================================
if __name__ == "__main__":
    ScreenBroadcaster().run()
