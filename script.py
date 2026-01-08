#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vkcloud_stealth_hunter.py

Скрипт для поиска IP в VK Cloud с эмуляцией поведения человека.
Реализует случайные задержки, перерывы, ночной режим, холостые запросы и подмену отпечатков.
"""

import ipaddress
import os
import sys
import time
import random
import threading
import datetime
import ssl
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util import ssl_

# Автоматическая загрузка переменных из .env файла
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openstack import connection
from openstack import exceptions as os_exc
from keystoneauth1 import exceptions as ks_exc

# Опциональная поддержка уведомлений через apprise
try:
    from apprise import Apprise
    APPRISE_AVAILABLE = True
except ImportError:
    APPRISE_AVAILABLE = False

# ========= КОНФИГУРАЦИЯ "ЧЕЛОВЕЧНОСТИ" =========
HUMAN_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"

# Диапазоны задержек
SLEEP_MIN = 10
SLEEP_MAX = 45

# Настройки перерывов
ATTEMPTS_BEFORE_BREAK_MIN = 5
ATTEMPTS_BEFORE_BREAK_MAX = 20
BREAK_DURATION_MIN = 120  # 2 минуты
BREAK_DURATION_MAX = 600  # 10 минут

# Задержка перед удалением неподходящего IP
DELETE_DELAY_MIN = 5
DELETE_DELAY_MAX = 15

# Часовой пояс МСК (UTC+3)
MSK_OFFSET = datetime.timezone(datetime.timedelta(hours=3))

# ========= TLS ADAPTER (ПУНКТ 11 - BASIC) =========
class CipherAdapter(HTTPAdapter):
    """
    Адаптер для изменения параметров SSL/TLS.
    Попытка сделать TLS Handshake чуть менее похожим на стандартный python-requests.
    """
    def init_poolmanager(self, connections, maxsize, block=False):
        context = ssl_.create_urllib3_context(ciphers=None) # Использовать дефолтные системные, но можно задать строку
        # Принудительно включаем TLS 1.2+
        context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        # Можно попробовать задать популярные шифры браузеров, но это зависит от OpenSSL системы
        # context.set_ciphers("ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...")
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=context
        )

# ========= НАСТРОЙКИ ПОДКЛЮЧЕНИЯ =========
def get_auth():
    auth = {
        "auth_url": os.getenv("VKCLOUD_AUTH_URL", "https://infra.mail.ru:35357/v3/"),
        "username": os.getenv("VKCLOUD_USERNAME"),
        "password": os.getenv("VKCLOUD_PASSWORD"),
        "project_id": os.getenv("VKCLOUD_PROJECT_ID"),
        "user_domain_name": os.getenv("VKCLOUD_USER_DOMAIN_NAME", "users"),
        "region_name": os.getenv("VKCLOUD_REGION_NAME", "RegionOne"),
        "interface": os.getenv("VKCLOUD_INTERFACE", "public"),
    }
    
    verify = os.getenv("VKCLOUD_VERIFY")
    if verify:
        if verify.lower() == "false":
            auth["verify"] = False
        else:
            auth["verify"] = verify
            
    required = ["username", "password", "project_id"]
    missing = [k for k in required if not auth.get(k)]
    if missing:
        raise SystemExit(f"❌ Нет переменных: {', '.join(f'VKCLOUD_{k.upper()}' for k in missing)}")
    return auth

# Переменные окружения для логики
SERVER_ID_OR_NAME = os.getenv("VKCLOUD_SERVER_ID_OR_NAME")
TARGET_NET_STR = os.getenv("VKCLOUD_TARGET_NET", "95.163.248.0/22")
TARGET_NETS_LIST = [ipaddress.ip_network(n.strip()) for n in TARGET_NET_STR.split(",") if n.strip()]
APPRISE_URL = os.getenv("VKCLOUD_APPRISE_URL")

# Принудительно 1 воркер для эмуляции человека
WORKERS_COUNT = 1 

# Глобальные переменные управления
stop_event = threading.Event()

# ========= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =========

def get_msk_time_multiplier():
    """Возвращает множитель задержки в зависимости от времени суток в МСК."""
    now_msk = datetime.datetime.now(MSK_OFFSET)
    hour = now_msk.hour
    
    # Ночной режим (23:00 - 09:00) - работаем в 3 раза медленнее
    if hour >= 23 or hour < 9:
        return 3.0
    return 1.0

def get_conn() -> connection.Connection:
    """Создает соединение с маскировкой под браузер."""
    auth_config = get_auth()
    conn = connection.Connection(**auth_config)
    
    # Маскировка User-Agent
    conn.session.user_agent = HUMAN_USER_AGENT
    
    # Применяем TLS адаптер
    conn.session.mount("https://", CipherAdapter())
    
    conn.authorize()
    return conn

def send_notification(title, body, type="info"):
    if not APPRISE_AVAILABLE or not APPRISE_URL:
        return
    try:
        apobj = Apprise()
        apobj.add(APPRISE_URL)
        emoji = "✅" if type == "success" else "ℹ️"
        if type == "error": emoji = "❌"
        apobj.notify(body=f"{emoji} {body}", title=title)
    except Exception:
        pass

def make_idle_noise(conn):
    """Совершает бессмысленные легитимные запросы (имитация просмотра панели)."""
    try:
        action = random.choice(['list_sgs', 'list_networks', 'check_limits'])
        print(f"   (👀 Холостой запрос: {action})")
        
        if action == 'list_sgs':
            list(conn.network.security_groups(limit=5))
        elif action == 'list_networks':
            list(conn.network.networks(limit=3))
        elif action == 'check_limits':
            conn.get_compute_limits()
            
    except Exception:
        pass # Игнорируем ошибки шума

def simulate_human_error(conn):
    """Имитирует ошибку человека (запрос несуществующего ресурса)."""
    print("   (🥴 Упс! Симуляция человеческой ошибки 404...)")
    try:
        # Пытаемся получить информацию о несуществующем сервере
        conn.compute.get_server("human-error-fake-uuid-12345")
    except Exception:
        pass # Ошибка ожидаема

# ========= ОСНОВНОЙ ВОРКЕР =========

def worker_logic():
    print(f"🤖 Запуск 'Human-like' бота. Целевые сети: {[str(n) for n in TARGET_NETS_LIST]}")
    send_notification("VK Cloud Hunter", "Бот запущен в скрытном режиме.", "info")
    
    conn = get_conn()
    
    # Поиск ресурсов (один раз)
    try:
        srv = conn.compute.find_server(SERVER_ID_OR_NAME)
        if not srv: raise Exception("Server not found")
        
        # Поиск порта
        ports = list(conn.network.ports(device_id=srv.id))
        if not ports: raise Exception("No ports found")
        target_port = ports[0]
        
        # Поиск внешней сети
        ext_net = None
        for n in conn.network.networks():
            if n.is_router_external:
                ext_net = n
                break
        if not ext_net: raise Exception("External network not found")
            
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        return

    # Счетчики
    attempt_counter = 0
    next_break_at = random.randint(ATTEMPTS_BEFORE_BREAK_MIN, ATTEMPTS_BEFORE_BREAK_MAX)
    
    while not stop_event.is_set():
        attempt_counter += 1
        
        # 1. Проверка на "Перекур"
        if attempt_counter >= next_break_at:
            break_time = random.randint(BREAK_DURATION_MIN, BREAK_DURATION_MAX)
            print(f"\n☕️ ПЕРЕРЫВ (имитация отхода от ПК) на {break_time // 60} мин {break_time % 60} сек...")
            send_notification("VK Cloud", "Ушел на перерыв (имитация человека)", "info")
            time.sleep(break_time)
            
            # Сброс счетчика до следующего перерыва
            attempt_counter = 0
            next_break_at = random.randint(ATTEMPTS_BEFORE_BREAK_MIN, ATTEMPTS_BEFORE_BREAK_MAX)
            print("▶️ Возвращаюсь к работе.\n")
            
            # Рефреш соединения после долгого простоя
            try:
                conn = get_conn()
            except:
                pass

        # 2. Симуляция ошибки (редко, ~2% шанс)
        if random.random() < 0.02:
            simulate_human_error(conn)
            time.sleep(random.uniform(2, 5))

        # 3. Холостой шум (перед действием, ~30% шанс)
        if random.random() < 0.3:
            make_idle_noise(conn)
            time.sleep(random.uniform(2, 8))

        # 4. Основное действие: Создание IP
        try:
            print(f"🔸 Попытка выделения IP...")
            fip = conn.network.create_ip(floating_network_id=ext_net.id)
            ip_addr = fip.floating_ip_address
            print(f"   🔹 Получен IP: {ip_addr}")
            
            # Проверка
            is_target = False
            try:
                ip_obj = ipaddress.ip_address(ip_addr)
                is_target = any(ip_obj in net for net in TARGET_NETS_LIST)
            except ValueError:
                pass
            
            if is_target:
                print(f"🎉 ДЖЕКПОТ! IP {ip_addr} подходит!")
                conn.network.update_ip(fip, port_id=target_port.id)
                send_notification("VK Cloud SUCCESS", f"Найден IP: {ip_addr}", "success")
                return # Успех, выход
                
            else:
                # 5. Задержка перед удалением (имитация "посмотрел, подумал")
                del_delay = random.uniform(DELETE_DELAY_MIN, DELETE_DELAY_MAX)
                print(f"   ❌ Не подходит. Удалю через {del_delay:.1f} сек...")
                time.sleep(del_delay)
                
                conn.network.delete_ip(fip)
                print("   🗑️ IP удален.")
        
        except Exception as e:
            print(f"⚠️ Ошибка в цикле: {e}")
            time.sleep(10)
            try: conn = get_conn()
            except: pass

        # 6. Пауза перед следующим циклом (с учетом времени суток)
        time_mult = get_msk_time_multiplier()
        base_sleep = random.uniform(SLEEP_MIN, SLEEP_MAX)
        final_sleep = base_sleep * time_mult
        
        mode_str = "🌙 Ночной режим" if time_mult > 1 else "☀️ Дневной режим"
        print(f"💤 Жду {final_sleep:.1f} сек ({mode_str})...")
        time.sleep(final_sleep)

if __name__ == "__main__":
    try:
        worker_logic()
    except KeyboardInterrupt:
        print("\n🛑 Остановлено вручную.")
