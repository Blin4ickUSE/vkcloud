#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎯 VK Cloud IP Hunter Pro v4.1 - Интерактивный скрипт

Полнофункциональный безопасный скрипт для поиска Floating IP:
- Мастер первоначальной настройки
- Интерактивное меню управления
- Безопасные человеческие задержки
- Уведомления в Telegram
- Статистика и логи

ВАЖНО: VK Cloud выдаёт подсеть на аккаунт автоматически.
Мы можем только ФИЛЬТРОВАТЬ выданные IP по нужным подсетям.
"""

import ipaddress
import os
import sys
import time
import random
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from pathlib import Path
from dataclasses import dataclass, field, asdict

try:
    from openstack import connection
    from openstack import exceptions as os_exc
    from keystoneauth1 import exceptions as ks_exc
    OPENSTACK_AVAILABLE = True
except ImportError:
    OPENSTACK_AVAILABLE = False

try:
    from apprise import Apprise
    APPRISE_AVAILABLE = True
except ImportError:
    APPRISE_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════
#                         КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════
VERSION = "4.1"
CONFIG_FILE = "hunter_config.json"
DB_FILE = "hunter_stats.db"
LOG_DIR = "logs"


# ═══════════════════════════════════════════════════════════════
#                      ЦВЕТА КОНСОЛИ
# ═══════════════════════════════════════════════════════════════
class C:
    """Цвета для консоли."""
    RST = "\033[0m"
    R = "\033[91m"      # Красный
    G = "\033[92m"      # Зелёный  
    Y = "\033[93m"      # Жёлтый
    B = "\033[94m"      # Синий
    M = "\033[95m"      # Пурпурный
    C = "\033[96m"      # Циан
    W = "\033[97m"      # Белый
    BOLD = "\033[1m"
    DIM = "\033[2m"


def clr(text: str, color: str) -> str:
    return f"{color}{text}{C.RST}"


def clear():
    os.system('cls' if os.name == 'nt' else 'clear')


def pause(msg: str = "Нажмите Enter..."):
    input(clr(f"\n{msg}", C.DIM))


# ═══════════════════════════════════════════════════════════════
#                       ЛОГИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════
class Logger:
    """Логгер с выводом в файл и консоль."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance
    
    def _init(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.log_file = f"{LOG_DIR}/hunt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.lock = threading.Lock()
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(message)s',
            datefmt='%H:%M:%S',
            handlers=[logging.FileHandler(self.log_file, encoding='utf-8')]
        )
        self._logger = logging.getLogger('hunter')
    
    def log(self, msg: str, level: str = "INFO", show: bool = True):
        ts = datetime.now().strftime("%H:%M:%S")
        
        colors = {
            "INFO": C.W, "OK": C.G, "WARN": C.Y, 
            "ERR": C.R, "DEBUG": C.DIM, "HUNT": C.M, "IP": C.C
        }
        color = colors.get(level, C.W)
        
        with self.lock:
            self._logger.info(f"[{level}] {msg}")
            if show:
                print(f"{C.DIM}{ts}{C.RST} {color}{msg}{C.RST}")


log = Logger()


# ═══════════════════════════════════════════════════════════════
#                  БЕЗОПАСНЫЕ ЗАДЕРЖКИ
# ═══════════════════════════════════════════════════════════════
@dataclass
class Delays:
    """
    Профиль безопасных задержек.
    ВСЕ ЗНАЧЕНИЯ ПОДОБРАНЫ ДЛЯ ЗАЩИТЫ ОТ БАНА!
    """
    # Между попытками выбивания (ГЛАВНОЕ!)
    attempt_min: float = 15.0       # Минимум 15 сек
    attempt_max: float = 35.0       # Максимум 35 сек
    
    # Случайность ±%
    jitter: float = 25.0
    
    # После получения IP (думаем как человек)
    after_get_min: float = 3.0
    after_get_max: float = 7.0
    
    # Перед привязкой (не спешим)
    before_bind_min: float = 5.0
    before_bind_max: float = 10.0
    
    # После удаления ненужного IP
    after_del_min: float = 8.0
    after_del_max: float = 15.0
    
    # После ошибки API (даём отдохнуть)
    error_min: float = 60.0
    error_max: float = 180.0
    
    # Между сессиями (МИНУТЫ!)
    session_min: int = 15
    session_max: int = 30
    
    # Попыток за сессию (мало = безопасно)
    max_attempts: int = 10
    
    # Между аккаунтами
    switch_min: float = 10.0
    switch_max: float = 25.0
    
    def attempt(self) -> float:
        """Задержка между попытками с jitter."""
        base = random.uniform(self.attempt_min, self.attempt_max)
        j = base * (self.jitter / 100) * random.uniform(-1, 1)
        return max(10.0, base + j)
    
    def after_get(self) -> float:
        return random.uniform(self.after_get_min, self.after_get_max)
    
    def before_bind(self) -> float:
        return random.uniform(self.before_bind_min, self.before_bind_max)
    
    def after_del(self) -> float:
        return random.uniform(self.after_del_min, self.after_del_max)
    
    def error(self) -> float:
        return random.uniform(self.error_min, self.error_max)
    
    def session(self) -> int:
        return random.randint(self.session_min, self.session_max)
    
    def switch(self) -> float:
        return random.uniform(self.switch_min, self.switch_max)


# ═══════════════════════════════════════════════════════════════
#                        АККАУНТ
# ═══════════════════════════════════════════════════════════════
@dataclass
class Account:
    """Аккаунт VK Cloud."""
    name: str
    username: str
    password: str
    project_id: str
    server: str
    
    # Расширенные настройки
    auth_url: str = "https://infra.mail.ru:35357/v3/"
    user_domain: str = "users"
    region: str = "RegionOne"
    ext_net: str = ""           # Имя внешней сети (пусто = авто)
    port_id: str = ""           # ID порта (пусто = первый порт ВМ)
    max_fip: int = 2
    enabled: bool = True
    
    # Runtime (не сохраняется)
    attempts: int = field(default=0, repr=False)
    success: int = field(default=0, repr=False)
    errors: int = field(default=0, repr=False)
    cooldown: Optional[datetime] = field(default=None, repr=False)
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "username": self.username,
            "password": self.password,
            "project_id": self.project_id,
            "server": self.server,
            "auth_url": self.auth_url,
            "user_domain": self.user_domain,
            "region": self.region,
            "ext_net": self.ext_net,
            "port_id": self.port_id,
            "max_fip": self.max_fip,
            "enabled": self.enabled
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> 'Account':
        return cls(
            name=d.get("name", "ACC"),
            username=d.get("username", ""),
            password=d.get("password", ""),
            project_id=d.get("project_id", ""),
            server=d.get("server", ""),
            auth_url=d.get("auth_url", "https://infra.mail.ru:35357/v3/"),
            user_domain=d.get("user_domain", "users"),
            region=d.get("region", "RegionOne"),
            ext_net=d.get("ext_net", ""),
            port_id=d.get("port_id", ""),
            max_fip=d.get("max_fip", 2),
            enabled=d.get("enabled", True)
        )
    
    def on_cooldown(self) -> bool:
        return self.cooldown and datetime.now() < self.cooldown
    
    def set_cooldown(self, mins: int):
        self.cooldown = datetime.now() + timedelta(minutes=mins)
        log.log(f"⏸️ [{self.name}] Cooldown {mins} мин", "WARN")


# ═══════════════════════════════════════════════════════════════
#                      КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════
@dataclass  
class Config:
    """Главная конфигурация."""
    accounts: List[Account] = field(default_factory=list)
    subnets: List[str] = field(default_factory=list)          # Целевые CIDR для фильтрации
    subnet_ids: List[str] = field(default_factory=list)       # ID подсетей для прямого запроса
    use_subnet_id: bool = False                                # Использовать subnet_id при создании
    tg_token: str = ""
    tg_chat: str = ""
    stop_on_success: bool = True
    delays: Delays = field(default_factory=Delays)
    
    def save(self):
        data = {
            "accounts": [a.to_dict() for a in self.accounts],
            "subnets": self.subnets,
            "subnet_ids": self.subnet_ids,
            "use_subnet_id": self.use_subnet_id,
            "tg_token": self.tg_token,
            "tg_chat": self.tg_chat,
            "stop_on_success": self.stop_on_success,
            "delays": asdict(self.delays)
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls) -> 'Config':
        if not os.path.exists(CONFIG_FILE):
            return cls()
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            cfg = cls()
            cfg.accounts = [Account.from_dict(a) for a in d.get("accounts", [])]
            cfg.subnets = d.get("subnets", [])
            cfg.subnet_ids = d.get("subnet_ids", [])
            cfg.use_subnet_id = d.get("use_subnet_id", False)
            cfg.tg_token = d.get("tg_token", "")
            cfg.tg_chat = d.get("tg_chat", "")
            cfg.stop_on_success = d.get("stop_on_success", True)
            if "delays" in d:
                cfg.delays = Delays(**d["delays"])
            return cfg
        except Exception as e:
            log.log(f"Ошибка загрузки конфига: {e}", "ERR")
            return cls()
    
    def get_networks(self) -> List[ipaddress.IPv4Network]:
        nets = []
        for s in self.subnets:
            try:
                nets.append(ipaddress.ip_network(s.strip()))
            except:
                pass
        return nets
    
    def is_configured(self) -> bool:
        return len(self.accounts) > 0 and len(self.subnets) > 0


# ═══════════════════════════════════════════════════════════════
#                     БАЗА ДАННЫХ
# ═══════════════════════════════════════════════════════════════
class DB:
    """SQLite для статистики."""
    
    def __init__(self):
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY, ip TEXT, acc TEXT, 
            action TEXT, subnet TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS captures (
            id INTEGER PRIMARY KEY, ip TEXT, acc TEXT,
            server TEXT, subnet TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP  
        )''')
        conn.commit()
        conn.close()
    
    def log_ip(self, ip: str, acc: str, action: str, subnet: str = None):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO log (ip, acc, action, subnet) VALUES (?,?,?,?)",
                  (ip, acc, action, subnet))
        conn.commit()
        conn.close()
    
    def capture(self, ip: str, acc: str, server: str, subnet: str):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO captures (ip, acc, server, subnet) VALUES (?,?,?,?)",
                  (ip, acc, server, subnet))
        conn.commit()
        conn.close()
    
    def get_captures(self, limit: int = 20) -> list:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT ip, acc, server, subnet, ts FROM captures ORDER BY id DESC LIMIT ?', (limit,))
        rows = c.fetchall()
        conn.close()
        return rows
    
    def get_stats(self) -> dict:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('SELECT subnet, COUNT(*) FROM log WHERE subnet IS NOT NULL GROUP BY subnet ORDER BY COUNT(*) DESC')
        rows = c.fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}


# ═══════════════════════════════════════════════════════════════
#                    VK CLOUD CLIENT
# ═══════════════════════════════════════════════════════════════
class VKClient:
    """Клиент VK Cloud."""
    
    def __init__(self, acc: Account, delays: Delays):
        self.acc = acc
        self.delays = delays
        self.conn = None
        self.server = None
        self.port = None
        self.ext_net = None
    
    def connect(self) -> bool:
        try:
            log.log(f"🔗 [{self.acc.name}] Подключение...", "INFO")
            
            self.conn = connection.Connection(
                auth_url=self.acc.auth_url,
                username=self.acc.username,
                password=self.acc.password,
                project_id=self.acc.project_id,
                user_domain_name=self.acc.user_domain,
                region_name=self.acc.region,
                interface="public"
            )
            self.conn.authorize()
            
            # ВМ
            srv = self.conn.compute.find_server(self.acc.server, ignore_missing=True)
            if not srv:
                log.log(f"❌ [{self.acc.name}] ВМ не найдена: {self.acc.server}", "ERR")
                return False
            self.server = self.conn.compute.get_server(srv.id)
            
            # Порт
            if self.acc.port_id:
                # Конкретный порт
                self.port = self.conn.network.get_port(self.acc.port_id)
                if not self.port:
                    log.log(f"❌ [{self.acc.name}] Порт не найден: {self.acc.port_id}", "ERR")
                    return False
            else:
                # Автопоиск порта
                ports = list(self.conn.network.ports(device_id=self.server.id))
                if not ports:
                    log.log(f"❌ [{self.acc.name}] Нет портов у ВМ", "ERR")
                    return False
                self.port = sorted(ports, key=lambda p: p.status != "ACTIVE")[0]
            
            # Внешняя сеть
            if self.acc.ext_net:
                # Конкретная сеть по имени
                self.ext_net = self.conn.network.find_network(self.acc.ext_net)
                if not self.ext_net:
                    log.log(f"❌ [{self.acc.name}] Сеть не найдена: {self.acc.ext_net}", "ERR")
                    return False
            else:
                # Автопоиск внешней сети
                for net in self.conn.network.networks():
                    if getattr(net, "is_router_external", False):
                        self.ext_net = net
                        break
            
            if not self.ext_net:
                log.log(f"❌ [{self.acc.name}] Внешняя сеть не найдена", "ERR")
                return False
            
            log.log(f"✅ [{self.acc.name}] OK → {self.server.name} | Сеть: {self.ext_net.name}", "OK")
            return True
            
        except Exception as e:
            log.log(f"❌ [{self.acc.name}] Ошибка: {e}", "ERR")
            return False
    
    # ═══════════════════════════════════════════════════════════
    #          "ЧЕЛОВЕЧЕСКИЕ" ДЕЙСТВИЯ - имитация работы в консоли
    # ═══════════════════════════════════════════════════════════
    
    def browse_networks(self):
        """Просмотр сетей (как человек смотрит в консоли)."""
        try:
            self.conn.authorize()
            list(self.conn.network.networks())
            log.log(f"👀 [{self.acc.name}] Просмотр сетей", "DEBUG", show=False)
        except:
            pass
    
    def browse_subnets(self):
        """Просмотр подсетей."""
        try:
            self.conn.authorize()
            list(self.conn.network.subnets())
            log.log(f"👀 [{self.acc.name}] Просмотр подсетей", "DEBUG", show=False)
        except:
            pass
    
    def browse_ports(self):
        """Просмотр портов."""
        try:
            self.conn.authorize()
            list(self.conn.network.ports(device_id=self.server.id))
            log.log(f"👀 [{self.acc.name}] Просмотр портов", "DEBUG", show=False)
        except:
            pass
    
    def browse_fips(self) -> list:
        """Просмотр списка Floating IP (GET /floatingips)."""
        try:
            self.conn.authorize()
            fips = list(self.conn.network.ips(project_id=self.acc.project_id))
            log.log(f"👀 [{self.acc.name}] Просмотр FIP ({len(fips)} шт)", "DEBUG", show=False)
            return fips
        except:
            return []
    
    def check_fip_status(self, fip_id: str):
        """Проверка статуса конкретного FIP (GET /floatingips/{id})."""
        try:
            self.conn.authorize()
            fip = self.conn.network.get_ip(fip_id)
            log.log(f"👀 [{self.acc.name}] Проверка FIP {fip_id[:8]}...", "DEBUG", show=False)
            return fip
        except:
            return None
    
    def browse_server(self):
        """Просмотр информации о ВМ."""
        try:
            self.conn.authorize()
            self.conn.compute.get_server(self.server.id)
            log.log(f"👀 [{self.acc.name}] Просмотр ВМ", "DEBUG", show=False)
        except:
            pass
    
    def random_browse(self):
        """Случайный 'просмотр' - как человек кликает по консоли."""
        actions = [
            self.browse_networks,
            self.browse_subnets, 
            self.browse_ports,
            self.browse_fips,
            self.browse_server,
        ]
        action = random.choice(actions)
        action()
    
    # ═══════════════════════════════════════════════════════════
    #                  РАБОТА С ПОДСЕТЯМИ
    # ═══════════════════════════════════════════════════════════
    
    def get_external_subnets(self) -> List[dict]:
        """Получить подсети внешней сети."""
        try:
            self.conn.authorize()
            subnets = []
            # Получаем ID подсетей из внешней сети
            subnet_ids = getattr(self.ext_net, 'subnets', [])
            for sid in subnet_ids:
                try:
                    sub = self.conn.network.get_subnet(sid)
                    if sub:
                        subnets.append({
                            'id': sub.id,
                            'name': sub.name,
                            'cidr': sub.cidr,
                        })
                except:
                    pass
            return subnets
        except:
            return []
    
    def find_subnet_by_cidr(self, target_cidr: str) -> Optional[str]:
        """Найти ID подсети по CIDR."""
        subnets = self.get_external_subnets()
        for s in subnets:
            if s['cidr'] == target_cidr:
                return s['id']
        return None
    
    # ═══════════════════════════════════════════════════════════
    
    def fip_count(self) -> int:
        try:
            self.conn.authorize()
            return len(list(self.conn.network.ips(project_id=self.acc.project_id)))
        except:
            return 99
    
    def can_create(self) -> bool:
        cnt = self.fip_count()
        ok = cnt < self.acc.max_fip
        log.log(f"📊 [{self.acc.name}] FIP: {cnt}/{self.acc.max_fip} {'✅' if ok else '❌'}", "INFO")
        return ok
    
    def allocate(self, subnet_id: str = None):
        """
        Выделить Floating IP.
        Если указан subnet_id - пытаемся из конкретной подсети.
        """
        self.conn.authorize()
        params = {"floating_network_id": self.ext_net.id}
        if subnet_id:
            params["subnet_id"] = subnet_id
        return self.conn.network.create_ip(**params)
    
    def release(self, fip):
        try:
            self.conn.authorize()
            self.conn.network.delete_ip(fip, ignore_missing=True)
        except Exception as e:
            log.log(f"⚠️ Ошибка удаления: {e}", "WARN", show=False)
    
    def bind(self, fip) -> bool:
        try:
            self.conn.authorize()
            self.conn.network.update_ip(fip, port_id=self.port.id)
            
            for _ in range(10):
                time.sleep(1)
                f = self.conn.network.get_ip(fip.id)
                if getattr(f, "port_id", None) == self.port.id:
                    return True
            return False
        except Exception as e:
            log.log(f"❌ Ошибка привязки: {e}", "ERR")
            return False


# ═══════════════════════════════════════════════════════════════
#                     УВЕДОМЛЕНИЯ
# ═══════════════════════════════════════════════════════════════
class Notify:
    """Telegram уведомления."""
    
    def __init__(self, cfg: Config):
        self.cfg = cfg
    
    def send(self, title: str, msg: str, success: bool = False):
        if not self.cfg.tg_token or not self.cfg.tg_chat:
            return
        if not APPRISE_AVAILABLE:
            return
        try:
            url = f"tgram://{self.cfg.tg_token}@telegram/{self.cfg.tg_chat}"
            ap = Apprise()
            ap.add(url)
            icon = "✅" if success else "ℹ️"
            ap.notify(body=f"{icon} {msg}", title=title)
        except:
            pass


# ═══════════════════════════════════════════════════════════════
#                       ОХОТНИК
# ═══════════════════════════════════════════════════════════════
class Hunter:
    """Главный класс охоты."""
    
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = DB()
        self.notify = Notify(cfg)
        self.clients: Dict[str, VKClient] = {}
        
        self.running = False
        self.total = 0
        self.session_num = 0
        self.session_att = 0
        self.start_time = None
    
    def init_clients(self) -> int:
        cnt = 0
        for acc in self.cfg.accounts:
            if not acc.enabled:
                continue
            client = VKClient(acc, self.cfg.delays)
            if client.connect():
                self.clients[acc.name] = client
                cnt += 1
            time.sleep(3)
        return cnt
    
    def detect_subnet(self, ip: str) -> Optional[str]:
        try:
            addr = ipaddress.ip_address(ip)
            for net in self.cfg.get_networks():
                if addr in net:
                    return str(net)
        except:
            pass
        return None
    
    def is_target(self, ip: str) -> bool:
        return self.detect_subnet(ip) is not None
    
    def get_account(self) -> Optional[str]:
        avail = []
        for name, client in self.clients.items():
            if client.acc.on_cooldown():
                continue
            if client.can_create():
                avail.append(name)
        return random.choice(avail) if avail else None
    
    def attempt(self, name: str) -> Optional[str]:
        client = self.clients[name]
        acc = client.acc
        
        acc.attempts += 1
        self.total += 1
        self.session_att += 1
        
        fip = None
        
        try:
            # ═══ ЧЕЛОВЕЧЕСКОЕ ПОВЕДЕНИЕ ═══
            if random.random() < 0.3:
                client.random_browse()
                time.sleep(random.uniform(1, 3))
            
            # ═══ ВЫБОР ПОДСЕТИ ═══
            subnet_id = None
            if self.cfg.use_subnet_id and self.cfg.subnet_ids:
                subnet_id = random.choice(self.cfg.subnet_ids)
                log.log(f"🎯 [{name}] Попытка #{self.session_att} → подсеть {subnet_id[:12]}...", "HUNT")
            else:
                log.log(f"🎯 [{name}] Попытка #{self.session_att} (всего: {self.total})", "HUNT")
            
            fip = client.allocate(subnet_id=subnet_id)
            
            ip = getattr(fip, "floating_ip_address", None)
            if not ip:
                log.log(f"⚠️ [{name}] FIP без адреса", "WARN")
                client.release(fip)
                return None
            
            subnet = self.detect_subnet(ip)
            log.log(f"📥 [{name}] Получен: {clr(ip, C.Y)} (подсеть: {subnet or '???'})", "IP")
            
            self.db.log_ip(ip, name, "GET", subnet)
            
            # Пауза после получения
            time.sleep(self.cfg.delays.after_get())
            
            # Иногда проверяем статус
            if random.random() < 0.2:
                client.check_fip_status(fip.id)
                time.sleep(random.uniform(0.5, 1.5))
            
            if self.is_target(ip):
                log.log(f"🎯 [{name}] {clr('ЦЕЛЕВОЙ!', C.G)} {ip} из {subnet}", "OK")
                
                # Пауза перед привязкой (человек думает)
                delay = self.cfg.delays.before_bind()
                log.log(f"⏳ Пауза {delay:.0f} сек перед привязкой...", "DEBUG")
                time.sleep(delay)
                
                # Иногда (25%) смотрим порты перед привязкой
                if random.random() < 0.25:
                    client.browse_ports()
                    time.sleep(random.uniform(1, 2))
                
                log.log(f"🔗 [{name}] Привязка к {client.server.name}...", "INFO")
                
                if client.bind(fip):
                    acc.success += 1
                    self.db.capture(ip, name, client.server.name, subnet)
                    
                    log.log(f"🎉 [{name}] {clr('УСПЕХ!', C.G)} {ip} привязан!", "OK")
                    
                    self.notify.send(
                        "🎯 IP захвачен!",
                        f"IP: {ip}\nПодсеть: {subnet}\nАккаунт: {name}\nВМ: {client.server.name}",
                        success=True
                    )
                    return ip
                else:
                    log.log(f"⚠️ [{name}] Привязка не подтверждена", "WARN")
                    client.release(fip)
            else:
                log.log(f"❌ [{name}] {ip} не целевой → удаляем", "INFO")
                
                # Иногда (15%) смотрим список FIP перед удалением
                if random.random() < 0.15:
                    client.browse_fips()
                    time.sleep(random.uniform(0.5, 1))
                
                time.sleep(self.cfg.delays.after_del())
                client.release(fip)
                
        except (ks_exc.Unauthorized, ks_exc.NotFound) as e:
            acc.errors += 1
            log.log(f"🔄 [{name}] Ошибка авторизации", "WARN")
            if fip:
                client.release(fip)
            acc.set_cooldown(random.randint(15, 30))
            
        except os_exc.HttpException as e:
            acc.errors += 1
            err = str(e).lower()
            log.log(f"⚠️ [{name}] HTTP ошибка: {e}", "ERR")
            if fip:
                client.release(fip)
            if "quota" in err or "limit" in err:
                acc.set_cooldown(random.randint(20, 40))
                
        except Exception as e:
            acc.errors += 1
            log.log(f"❌ [{name}] Ошибка: {e}", "ERR")
            if fip:
                client.release(fip)
        
        return None
    
    def run_session(self) -> Optional[str]:
        self.session_num += 1
        self.session_att = 0
        max_att = self.cfg.delays.max_attempts
        
        log.log(f"\n{'═'*50}", "INFO")
        log.log(f"📍 СЕССИЯ #{self.session_num} (макс {max_att} попыток)", "INFO")
        log.log(f"{'═'*50}\n", "INFO")
        
        while self.running and self.session_att < max_att:
            name = self.get_account()
            
            if not name:
                log.log("⏸️ Все аккаунты заняты, жду 60 сек...", "WARN")
                time.sleep(60)
                continue
            
            result = self.attempt(name)
            if result:
                return result
            
            # ГЛАВНАЯ ПАУЗА
            delay = self.cfg.delays.attempt()
            log.log(f"💤 Пауза {delay:.0f} сек...", "DEBUG")
            
            for _ in range(int(delay)):
                if not self.running:
                    return None
                time.sleep(1)
            
            # Иногда меняем аккаунт
            if len(self.clients) > 1 and random.random() < 0.15:
                sw = self.cfg.delays.switch()
                log.log(f"🔄 Смена аккаунта, пауза {sw:.0f} сек", "DEBUG")
                time.sleep(sw)
        
        return None
    
    def run(self):
        self.running = True
        self.start_time = datetime.now()
        
        log.log(f"\n{'🎯'*25}", "INFO")
        log.log(f"   VK CLOUD IP HUNTER v{VERSION}", "INFO")
        log.log(f"   Подсети: {', '.join(self.cfg.subnets)}", "INFO")
        log.log(f"{'🎯'*25}\n", "INFO")
        
        cnt = self.init_clients()
        if cnt == 0:
            log.log("❌ Нет доступных аккаунтов!", "ERR")
            return
        
        log.log(f"✅ Аккаунтов: {cnt}", "OK")
        
        self.notify.send("🚀 Охота запущена", f"Аккаунтов: {cnt}")
        
        try:
            while self.running:
                result = self.run_session()
                
                if result:
                    if self.cfg.stop_on_success:
                        log.log(f"\n🏆 ЦЕЛЬ: {result}", "OK")
                        break
                
                if not self.running:
                    break
                
                pause_min = self.cfg.delays.session()
                log.log(f"\n☕ Пауза {pause_min} мин между сессиями", "INFO")
                
                self.notify.send("⏸️ Пауза", f"Сессия #{self.session_num} → пауза {pause_min} мин")
                
                for i in range(pause_min * 60):
                    if not self.running:
                        break
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            log.log("\n🛑 Остановлено (Ctrl+C)", "WARN")
        
        self.running = False
        elapsed = datetime.now() - self.start_time
        log.log(f"\n📊 Итого: {self.total} попыток за {elapsed}", "INFO")
    
    def stop(self):
        self.running = False


# ═══════════════════════════════════════════════════════════════
#                   МАСТЕР НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════
def setup_wizard() -> Config:
    """Мастер первоначальной настройки."""
    clear()
    print(clr("""
╔═══════════════════════════════════════════════════════════════╗
║           🧙 МАСТЕР ПЕРВОНАЧАЛЬНОЙ НАСТРОЙКИ                  ║
╚═══════════════════════════════════════════════════════════════╝
    """, C.C))
    
    print(clr("Добро пожаловать! Настроим скрипт за несколько шагов.\n", C.W))
    
    cfg = Config()
    
    # ШАГ 1: Аккаунт
    print(clr("═══ ШАГ 1: АККАУНТ VK CLOUD ═══\n", C.Y))
    print("Где взять данные:")
    print("  • Auth URL      - https://infra.mail.ru:35357/v3/ (стандартный)")
    print("  • Email/Пароль  - ваши учётные данные VK Cloud")
    print("  • Project ID    - Консоль → Настройки проекта → Project ID")
    print("  • User Domain   - обычно 'users'")
    print("  • Region        - обычно 'RegionOne'")
    print("  • Имя ВМ        - Облачные вычисления → Виртуальные машины")
    print("  • Внешняя сеть  - обычно 'internet' или 'ext-net'")
    print()
    
    while True:
        print(clr("─── Введите данные аккаунта ───\n", C.BOLD))
        
        name = input(clr("  Имя аккаунта [ACC1]: ", C.W)).strip().upper() or "ACC1"
        
        auth_url = input(clr("  Auth URL [https://infra.mail.ru:35357/v3/]: ", C.W)).strip()
        auth_url = auth_url or "https://infra.mail.ru:35357/v3/"
        
        username = input(clr("  Email: ", C.W)).strip()
        password = input(clr("  Пароль: ", C.W)).strip()
        project_id = input(clr("  Project ID: ", C.W)).strip()
        
        user_domain = input(clr("  User Domain [users]: ", C.W)).strip() or "users"
        region = input(clr("  Region [RegionOne]: ", C.W)).strip() or "RegionOne"
        
        server = input(clr("  Имя ВМ (или ID): ", C.W)).strip()
        ext_net = input(clr("  Внешняя сеть [auto]: ", C.W)).strip()
        port_id = input(clr("  Port ID [auto]: ", C.W)).strip()
        
        if not all([username, password, project_id, server]):
            print(clr("\n❌ Email, пароль, Project ID и имя ВМ обязательны!", C.R))
            continue
        
        cfg.accounts.append(Account(
            name=name, 
            username=username, 
            password=password,
            project_id=project_id, 
            server=server,
            auth_url=auth_url,
            user_domain=user_domain,
            region=region,
            ext_net=ext_net,
            port_id=port_id
        ))
        print(clr(f"\n✅ Аккаунт {name} добавлен!", C.G))
        
        # ═══ ПОКАЗАТЬ ПОДСЕТИ ИЗ VK CLOUD ═══
        if OPENSTACK_AVAILABLE:
            show_subs = input(clr("\nПодключиться и показать доступные подсети? (Y/n): ", C.W)).strip().lower()
            if show_subs != 'n':
                print(clr("\n🔍 Подключение к VK Cloud...", C.Y))
                try:
                    acc = cfg.accounts[-1]  # Последний добавленный
                    client = VKClient(acc, Delays())
                    if client.connect():
                        subnets = client.get_external_subnets()
                        if subnets:
                            print(clr(f"\n📋 Доступные подсети ({client.ext_net.name}):\n", C.G))
                            for i, s in enumerate(subnets, 1):
                                print(f"   {i}. CIDR: {s['cidr']:20} ID: {s['id']}")
                            
                            # Предложить добавить subnet_id
                            print(clr("\n💡 Можете выбрать подсеть для прямого запроса:", C.DIM))
                            choice = input(clr("   Номер подсети (Enter = пропустить): ", C.W)).strip()
                            if choice:
                                try:
                                    idx = int(choice) - 1
                                    if 0 <= idx < len(subnets):
                                        cfg.subnet_ids.append(subnets[idx]['id'])
                                        cfg.use_subnet_id = True
                                        print(clr(f"   ✅ Subnet ID добавлен: {subnets[idx]['cidr']}", C.G))
                                except:
                                    pass
                        else:
                            print(clr("   Подсети не найдены", C.Y))
                    else:
                        print(clr("   ❌ Не удалось подключиться", C.R))
                except Exception as e:
                    print(clr(f"   ❌ Ошибка: {e}", C.R))
        
        more = input(clr("\nДобавить ещё аккаунт? (y/N): ", C.W)).strip().lower()
        if more != 'y':
            break
        print()
    
    # ШАГ 2: Подсети для фильтрации
    print(clr("\n═══ ШАГ 2: ЦЕЛЕВЫЕ ПОДСЕТИ (фильтр) ═══\n", C.Y))
    
    if cfg.subnet_ids:
        print(clr("У вас уже выбран Subnet ID для прямого запроса.", C.G))
        print("Но всё равно укажите подсети для фильтрации результата:\n")
    
    print("Введите CIDR подсетей которые вы хотите ловить.")
    print("Формат: xxx.xxx.xxx.0/24")
    print("Подсети можно посмотреть выше при подключении к VK Cloud.\n")
    
    while True:
        subnet = input(clr("Подсеть (пусто = готово): ", C.W)).strip()
        if not subnet:
            if cfg.subnets:
                break
            print(clr("Нужна хотя бы одна подсеть!", C.R))
            continue
        
        try:
            ipaddress.ip_network(subnet)
            if subnet not in cfg.subnets:
                cfg.subnets.append(subnet)
                print(clr(f"  ✅ Добавлено: {subnet}", C.G))
            else:
                print(clr("  ⚠️ Уже есть", C.Y))
        except:
            print(clr("  ❌ Неверный формат (пример: 95.163.248.0/24)", C.R))
    
    # ШАГ 3: Telegram (опционально)
    print(clr("\n═══ ШАГ 3: TELEGRAM (опционально) ═══\n", C.Y))
    
    setup_tg = input(clr("Настроить Telegram уведомления? (y/N): ", C.W)).strip().lower()
    if setup_tg == 'y':
        print("\nКак получить токен:")
        print("  1. Откройте @BotFather в Telegram")
        print("  2. Отправьте /newbot")
        print("  3. Скопируйте токен\n")
        
        cfg.tg_token = input(clr("Токен бота: ", C.W)).strip()
        
        print("\nКак получить Chat ID:")
        print("  1. Откройте @userinfobot в Telegram")
        print("  2. Скопируйте ваш ID\n")
        
        cfg.tg_chat = input(clr("Chat ID: ", C.W)).strip()
    
    # Сохраняем
    cfg.save()
    
    print(clr("\n═══════════════════════════════════════════════════", C.G))
    print(clr("✅ НАСТРОЙКА ЗАВЕРШЕНА!", C.G))
    print(clr("═══════════════════════════════════════════════════\n", C.G))
    
    print(f"  Аккаунтов: {len(cfg.accounts)}")
    print(f"  Подсетей (фильтр): {len(cfg.subnets)}")
    print(f"  Subnet ID (прямой): {len(cfg.subnet_ids)} {'✅' if cfg.use_subnet_id else ''}")
    print(f"  Telegram: {'✅' if cfg.tg_token else '❌'}")
    
    pause()
    return cfg


# ═══════════════════════════════════════════════════════════════
#                   ИНТЕРАКТИВНОЕ МЕНЮ
# ═══════════════════════════════════════════════════════════════
class Menu:
    """Главное меню."""
    
    def __init__(self):
        self.cfg = Config.load()
        self.hunter: Optional[Hunter] = None
    
    def header(self):
        clear()
        print(clr(f"""
╔═══════════════════════════════════════════════════════════════╗
║              🎯 VK CLOUD IP HUNTER v{VERSION}                     ║
╚═══════════════════════════════════════════════════════════════╝
        """, C.C))
    
    def status(self):
        print(clr("📊 СТАТУС:", C.BOLD))
        acc_cnt = len([a for a in self.cfg.accounts if a.enabled])
        print(f"   Аккаунтов: {acc_cnt}")
        print(f"   Подсетей: {len(self.cfg.subnets)}")
        print(f"   Subnet ID: {'✅ '+str(len(self.cfg.subnet_ids)) if self.cfg.use_subnet_id else '❌'}")
        print(f"   Telegram: {'✅' if self.cfg.tg_token else '❌'}")
        
        if self.cfg.subnets:
            subs = ', '.join(self.cfg.subnets[:2])
            if len(self.cfg.subnets) > 2:
                subs += f" +{len(self.cfg.subnets)-2}"
            print(f"   Цели: {subs}")
    
    def main(self):
        # Первый запуск - мастер настройки
        if not self.cfg.is_configured():
            self.cfg = setup_wizard()
        
        while True:
            self.header()
            self.status()
            
            print(clr("\n📋 МЕНЮ:", C.BOLD))
            print("   1. 👥 Аккаунты")
            print("   2. 🎯 Подсети (фильтр)")
            print("   3. 🔌 Subnet ID (прямой запрос)")
            print("   4. 📱 Telegram")
            print("   5. ⚙️  Задержки")
            print("   6. 🚀 ЗАПУСТИТЬ ОХОТУ")
            print("   7. 📊 Статистика")
            print("   8. 📜 Логи")
            print("   0. 🚪 Выход")
            
            ch = input(clr("\n➤ ", C.Y)).strip()
            
            if ch == "1": self.accounts()
            elif ch == "2": self.subnets()
            elif ch == "3": self.subnet_ids_menu()
            elif ch == "4": self.telegram()
            elif ch == "5": self.delays()
            elif ch == "6": self.hunt()
            elif ch == "7": self.stats()
            elif ch == "8": self.logs()
            elif ch == "0": break
    
    def accounts(self):
        while True:
            self.header()
            print(clr("👥 АККАУНТЫ:\n", C.BOLD))
            
            if not self.cfg.accounts:
                print("   (нет аккаунтов)")
            else:
                for i, a in enumerate(self.cfg.accounts, 1):
                    st = "✅" if a.enabled else "❌"
                    print(f"   {i}. {st} {a.name}: {a.username} → {a.server}")
            
            print(clr("\n   A=добавить  D=удалить  T=вкл/выкл  E=редактировать  0=назад", C.DIM))
            
            ch = input(clr("\n➤ ", C.Y)).strip().upper()
            
            if ch == "A":
                print(clr("\n─── Основные данные ───", C.DIM))
                name = input("   Имя: ").strip().upper() or f"ACC{len(self.cfg.accounts)+1}"
                username = input("   Email: ").strip()
                password = input("   Пароль: ").strip()
                project_id = input("   Project ID: ").strip()
                server = input("   Имя ВМ: ").strip()
                
                if not all([username, password, project_id, server]):
                    print(clr("   ❌ Все поля обязательны!", C.R))
                    pause()
                    continue
                
                print(clr("\n─── Расширенные (Enter = по умолчанию) ───", C.DIM))
                auth_url = input("   Auth URL [https://infra.mail.ru:35357/v3/]: ").strip()
                auth_url = auth_url or "https://infra.mail.ru:35357/v3/"
                user_domain = input("   User Domain [users]: ").strip() or "users"
                region = input("   Region [RegionOne]: ").strip() or "RegionOne"
                ext_net = input("   Внешняя сеть [auto]: ").strip()
                port_id = input("   Port ID [auto]: ").strip()
                
                self.cfg.accounts.append(Account(
                    name=name, username=username, password=password,
                    project_id=project_id, server=server,
                    auth_url=auth_url, user_domain=user_domain, region=region,
                    ext_net=ext_net, port_id=port_id
                ))
                self.cfg.save()
                print(clr(f"   ✅ {name} добавлен!", C.G))
                pause()
            elif ch == "E":
                if not self.cfg.accounts:
                    continue
                try:
                    idx = int(input("   Номер: ")) - 1
                    if 0 <= idx < len(self.cfg.accounts):
                        a = self.cfg.accounts[idx]
                        print(clr(f"\n─── Редактирование {a.name} (Enter = оставить) ───", C.DIM))
                        
                        new_user = input(f"   Email [{a.username}]: ").strip()
                        if new_user: a.username = new_user
                        
                        new_pass = input(f"   Пароль [***]: ").strip()
                        if new_pass: a.password = new_pass
                        
                        new_proj = input(f"   Project ID [{a.project_id}]: ").strip()
                        if new_proj: a.project_id = new_proj
                        
                        new_srv = input(f"   Имя ВМ [{a.server}]: ").strip()
                        if new_srv: a.server = new_srv
                        
                        new_auth = input(f"   Auth URL [{a.auth_url}]: ").strip()
                        if new_auth: a.auth_url = new_auth
                        
                        new_dom = input(f"   User Domain [{a.user_domain}]: ").strip()
                        if new_dom: a.user_domain = new_dom
                        
                        new_reg = input(f"   Region [{a.region}]: ").strip()
                        if new_reg: a.region = new_reg
                        
                        new_net = input(f"   Внешняя сеть [{a.ext_net or 'auto'}]: ").strip()
                        a.ext_net = new_net
                        
                        new_port = input(f"   Port ID [{a.port_id or 'auto'}]: ").strip()
                        a.port_id = new_port
                        
                        self.cfg.save()
                        print(clr(f"   ✅ Сохранено!", C.G))
                except: pass
                pause()
            elif ch == "D":
                try:
                    idx = int(input("   Номер: ")) - 1
                    if 0 <= idx < len(self.cfg.accounts):
                        removed = self.cfg.accounts.pop(idx)
                        self.cfg.save()
                        print(clr(f"   ✅ {removed.name} удалён", C.G))
                except: pass
                pause()
            elif ch == "T":
                try:
                    idx = int(input("   Номер: ")) - 1
                    if 0 <= idx < len(self.cfg.accounts):
                        self.cfg.accounts[idx].enabled = not self.cfg.accounts[idx].enabled
                        self.cfg.save()
                except: pass
            elif ch == "0":
                break
    
    def subnets(self):
        while True:
            self.header()
            print(clr("🎯 ЦЕЛЕВЫЕ ПОДСЕТИ (фильтр по CIDR):\n", C.BOLD))
            print(clr("   Используются для фильтрации полученных IP\n", C.DIM))
            
            if not self.cfg.subnets:
                print("   (нет подсетей)")
            else:
                for i, s in enumerate(self.cfg.subnets, 1):
                    print(f"   {i}. {s}")
            
            print(clr("\n   A=добавить  D=удалить  C=очистить  0=назад", C.DIM))
            
            ch = input(clr("\n➤ ", C.Y)).strip().upper()
            
            if ch == "A":
                subnet = input("   Подсеть (CIDR): ").strip()
                try:
                    ipaddress.ip_network(subnet)
                    if subnet not in self.cfg.subnets:
                        self.cfg.subnets.append(subnet)
                        self.cfg.save()
                        print(clr(f"   ✅ Добавлено", C.G))
                except:
                    print(clr("   ❌ Неверный формат", C.R))
                pause()
            elif ch == "D":
                try:
                    idx = int(input("   Номер: ")) - 1
                    if 0 <= idx < len(self.cfg.subnets):
                        self.cfg.subnets.pop(idx)
                        self.cfg.save()
                except: pass
            elif ch == "C":
                self.cfg.subnets = []
                self.cfg.save()
            elif ch == "0":
                break
    
    def subnet_ids_menu(self):
        """Меню для прямого запроса подсети по subnet_id."""
        while True:
            self.header()
            print(clr("🔌 SUBNET ID (прямой запрос подсети):\n", C.BOLD))
            print(clr("   Запрос FIP из конкретной подсети через API параметр subnet_id\n", C.DIM))
            
            print(f"   Режим: {'✅ ВКЛЮЧЁН' if self.cfg.use_subnet_id else '❌ ВЫКЛЮЧЕН'}")
            print()
            
            if not self.cfg.subnet_ids:
                print("   (нет subnet_id)")
            else:
                for i, sid in enumerate(self.cfg.subnet_ids, 1):
                    print(f"   {i}. {sid}")
            
            print(clr("\n   T=вкл/выкл  A=добавить  D=удалить  S=показать доступные  0=назад", C.DIM))
            
            ch = input(clr("\n➤ ", C.Y)).strip().upper()
            
            if ch == "T":
                self.cfg.use_subnet_id = not self.cfg.use_subnet_id
                self.cfg.save()
                state = "ВКЛЮЧЁН" if self.cfg.use_subnet_id else "ВЫКЛЮЧЕН"
                print(clr(f"   ✅ Режим subnet_id: {state}", C.G))
                pause()
            elif ch == "A":
                sid = input("   Subnet ID: ").strip()
                if sid and sid not in self.cfg.subnet_ids:
                    self.cfg.subnet_ids.append(sid)
                    self.cfg.save()
                    print(clr(f"   ✅ Добавлено", C.G))
                pause()
            elif ch == "D":
                try:
                    idx = int(input("   Номер: ")) - 1
                    if 0 <= idx < len(self.cfg.subnet_ids):
                        self.cfg.subnet_ids.pop(idx)
                        self.cfg.save()
                except: pass
            elif ch == "S":
                self.show_available_subnets()
            elif ch == "0":
                break
    
    def show_available_subnets(self):
        """Показать доступные подсети внешней сети."""
        if not self.cfg.accounts:
            print(clr("\n   ❌ Сначала добавьте аккаунт!", C.R))
            pause()
            return
        
        if not OPENSTACK_AVAILABLE:
            print(clr("\n   ❌ OpenStack SDK не установлен", C.R))
            pause()
            return
        
        print(clr("\n   🔍 Получение подсетей...", C.Y))
        
        acc = self.cfg.accounts[0]
        try:
            client = VKClient(acc, self.cfg.delays)
            if client.connect():
                subnets = client.get_external_subnets()
                
                print(clr(f"\n   📋 Подсети внешней сети ({client.ext_net.name}):\n", C.G))
                for s in subnets:
                    print(f"      CIDR: {s['cidr']:20} ID: {s['id']}")
                
                print(clr("\n   💡 Скопируйте нужный ID и добавьте через 'A'", C.DIM))
            else:
                print(clr("\n   ❌ Не удалось подключиться", C.R))
        except Exception as e:
            print(clr(f"\n   ❌ Ошибка: {e}", C.R))
        
        pause()
    
    def telegram(self):
        self.header()
        print(clr("📱 TELEGRAM:\n", C.BOLD))
        
        print(f"   Токен: {'***'+self.cfg.tg_token[-10:] if self.cfg.tg_token else '(нет)'}")
        print(f"   Chat ID: {self.cfg.tg_chat or '(нет)'}")
        
        print(clr("\n   1=токен  2=chat_id  3=тест  0=назад", C.DIM))
        
        ch = input(clr("\n➤ ", C.Y)).strip()
        
        if ch == "1":
            self.cfg.tg_token = input("   Токен: ").strip()
            self.cfg.save()
        elif ch == "2":
            self.cfg.tg_chat = input("   Chat ID: ").strip()
            self.cfg.save()
        elif ch == "3":
            n = Notify(self.cfg)
            n.send("🧪 Тест", "Тестовое сообщение от VK Hunter")
            print(clr("   ✅ Отправлено", C.G))
            pause()
    
    def delays(self):
        self.header()
        d = self.cfg.delays
        
        print(clr("⚙️ ЗАДЕРЖКИ (защита от бана):\n", C.BOLD))
        print(f"   1. Между попытками: {d.attempt_min:.0f}-{d.attempt_max:.0f} сек")
        print(f"   2. После ошибки: {d.error_min:.0f}-{d.error_max:.0f} сек")
        print(f"   3. Пауза сессий: {d.session_min}-{d.session_max} мин")
        print(f"   4. Попыток/сессия: {d.max_attempts}")
        print(f"   5. Стоп при успехе: {'✅' if self.cfg.stop_on_success else '❌'}")
        
        print(clr("\n   Введите номер для изменения, 0=назад", C.DIM))
        
        ch = input(clr("\n➤ ", C.Y)).strip()
        
        try:
            if ch == "1":
                d.attempt_min = float(input("   Мин сек: ") or d.attempt_min)
                d.attempt_max = float(input("   Макс сек: ") or d.attempt_max)
            elif ch == "2":
                d.error_min = float(input("   Мин сек: ") or d.error_min)
                d.error_max = float(input("   Макс сек: ") or d.error_max)
            elif ch == "3":
                d.session_min = int(input("   Мин мин: ") or d.session_min)
                d.session_max = int(input("   Макс мин: ") or d.session_max)
            elif ch == "4":
                d.max_attempts = int(input("   Попыток: ") or d.max_attempts)
            elif ch == "5":
                self.cfg.stop_on_success = not self.cfg.stop_on_success
            self.cfg.save()
        except: pass
    
    def hunt(self):
        if not self.cfg.accounts:
            print(clr("\n❌ Добавьте аккаунт!", C.R))
            pause()
            return
        if not self.cfg.subnets:
            print(clr("\n❌ Добавьте подсеть!", C.R))
            pause()
            return
        if not OPENSTACK_AVAILABLE:
            print(clr("\n❌ pip install openstacksdk", C.R))
            pause()
            return
        
        clear()
        print(clr("\n🚀 ЗАПУСК ОХОТЫ", C.G))
        print(clr("   Ctrl+C для остановки\n", C.Y))
        
        self.hunter = Hunter(self.cfg)
        self.hunter.run()
        
        pause()
    
    def stats(self):
        self.header()
        db = DB()
        
        print(clr("📊 СТАТИСТИКА:\n", C.BOLD))
        
        captures = db.get_captures(10)
        print(clr("🏆 Последние захваты:", C.Y))
        if captures:
            for ip, acc, srv, sub, ts in captures:
                print(f"   {ts} | {ip} ({sub}) → {acc}")
        else:
            print("   (нет)")
        
        stats = db.get_stats()
        if stats:
            print(clr("\n📈 По подсетям:", C.Y))
            for sub, cnt in list(stats.items())[:5]:
                print(f"   {sub}: {cnt}")
        
        pause()
    
    def logs(self):
        self.header()
        print(clr("📜 ЛОГИ:\n", C.BOLD))
        
        logs = sorted(Path(LOG_DIR).glob("*.log"), reverse=True)[:5]
        
        if not logs:
            print("   (нет логов)")
        else:
            for i, f in enumerate(logs, 1):
                print(f"   {i}. {f.name}")
            
            ch = input("\n   Номер (0=назад): ").strip()
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(logs):
                    clear()
                    with open(logs[idx], 'r', encoding='utf-8') as f:
                        for line in f.readlines()[-40:]:
                            print(line.rstrip())
            except: pass
        
        pause()


# ═══════════════════════════════════════════════════════════════
#                        MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    if not OPENSTACK_AVAILABLE:
        print(clr("\n⚠️ Установите зависимости:", C.Y))
        print("   pip install openstacksdk python-dotenv apprise\n")
    
    menu = Menu()
    menu.main()


if __name__ == "__main__":
    main()
