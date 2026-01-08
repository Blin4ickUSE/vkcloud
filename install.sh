#!/bin/bash
#
# VK Cloud IP Hunter Pro - Установщик
# 
# Использование:
#   curl -sL https://raw.githubusercontent.com/wrx861/vkcloud/main/install.sh | sudo bash
#
# или:
#   wget -qO- https://raw.githubusercontent.com/wrx861/vkcloud/main/install.sh | sudo bash
#

set -e

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="/opt/vkcloud"
REPO_URL="https://github.com/blin4ickuse/vkcloud.git"
COMMAND_NAME="vkcloud"

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║        🎯 VK Cloud IP Hunter Pro - Установщик                 ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Проверка root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите с правами root (sudo)${NC}"
    echo "   sudo bash install.sh"
    exit 1
fi

# Определение пакетного менеджера
if command -v apt-get &> /dev/null; then
    PKG_MANAGER="apt"
elif command -v dnf &> /dev/null; then
    PKG_MANAGER="dnf"
elif command -v yum &> /dev/null; then
    PKG_MANAGER="yum"
else
    echo -e "${RED}❌ Не найден пакетный менеджер (apt/dnf/yum)${NC}"
    exit 1
fi

echo -e "${YELLOW}📦 Проверка и установка зависимостей...${NC}"

# Обновление списка пакетов (только для apt)
if [ "$PKG_MANAGER" = "apt" ]; then
    apt-get update -qq
fi

# Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}   Установка Python 3...${NC}"
    if [ "$PKG_MANAGER" = "apt" ]; then
        apt-get install -y python3 python3-full
    else
        $PKG_MANAGER install -y python3
    fi
fi
PYTHON_VERSION=$(python3 --version 2>&1)
echo -e "${GREEN}✅ $PYTHON_VERSION${NC}"

# python3-venv (критично для Python 3.12+)
if [ "$PKG_MANAGER" = "apt" ]; then
    # Получаем версию Python для правильного имени пакета
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    VENV_PKG="python${PY_VER}-venv"
    
    # Проверяем, установлен ли venv
    if ! python3 -c "import ensurepip" &> /dev/null; then
        echo -e "${YELLOW}   Установка ${VENV_PKG}...${NC}"
        apt-get install -y $VENV_PKG python3-pip
    fi
fi
echo -e "${GREEN}✅ python3-venv установлен${NC}"

# git
if ! command -v git &> /dev/null; then
    echo -e "${YELLOW}   Установка git...${NC}"
    if [ "$PKG_MANAGER" = "apt" ]; then
        apt-get install -y git
    else
        $PKG_MANAGER install -y git
    fi
fi
echo -e "${GREEN}✅ git установлен${NC}"

# Удаление старой версии
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}🗑️  Удаление старой версии...${NC}"
    rm -rf "$INSTALL_DIR"
fi

# Удаление старой команды
if [ -f "/usr/local/bin/$COMMAND_NAME" ]; then
    rm -f "/usr/local/bin/$COMMAND_NAME"
fi

# Клонирование репозитория
echo -e "${YELLOW}📥 Скачивание...${NC}"
git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"

# Создание виртуального окружения
echo -e "${YELLOW}🐍 Создание виртуального окружения...${NC}"
python3 -m venv "$INSTALL_DIR/venv"
echo -e "${GREEN}✅ venv создан${NC}"

# Установка Python зависимостей
echo -e "${YELLOW}📦 Установка Python библиотек...${NC}"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet 2>/dev/null
"$INSTALL_DIR/venv/bin/pip" install openstacksdk python-dotenv apprise colorama blessed --quiet
echo -e "${GREEN}✅ Библиотеки установлены${NC}"

# Создание команды vkcloud
echo -e "${YELLOW}🔧 Создание команды ${COMMAND_NAME}...${NC}"

cat > /usr/local/bin/$COMMAND_NAME << 'SCRIPT'
#!/bin/bash
cd /opt/vkcloud
./venv/bin/python hunter_pro.py "$@"
SCRIPT

chmod +x /usr/local/bin/$COMMAND_NAME

# Создание директорий
mkdir -p "$INSTALL_DIR/logs"

# Права
chmod +x "$INSTALL_DIR/hunter_pro.py"

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ УСТАНОВКА ЗАВЕРШЕНА!                    ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   Директория: ${CYAN}$INSTALL_DIR${NC}"
echo -e "   Команда:    ${CYAN}$COMMAND_NAME${NC}"
echo ""
echo -e "${YELLOW}🚀 Запуск:${NC}"
echo -e "   ${CYAN}vkcloud${NC}"
echo ""
echo -e "${YELLOW}📁 Файлы:${NC}"
echo -e "   Конфиг:     ${CYAN}$INSTALL_DIR/hunter_config.json${NC}"
echo -e "   Логи:       ${CYAN}$INSTALL_DIR/logs/${NC}"
echo -e "   Статистика: ${CYAN}$INSTALL_DIR/hunter_stats.db${NC}"
echo ""
