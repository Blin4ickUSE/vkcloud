#!/bin/bash
#
# VK Cloud IP Hunter Pro - Удаление
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="/opt/vkcloud"
COMMAND_NAME="vkcloud"

echo -e "${YELLOW}🗑️ Удаление VK Cloud IP Hunter...${NC}"

# Проверка root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите с правами root (sudo)${NC}"
    exit 1
fi

# Удаление команды
if [ -f "/usr/local/bin/$COMMAND_NAME" ]; then
    rm -f "/usr/local/bin/$COMMAND_NAME"
    echo -e "${GREEN}✅ Команда $COMMAND_NAME удалена${NC}"
fi

# Удаление директории
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo -e "${GREEN}✅ Директория $INSTALL_DIR удалена${NC}"
fi

echo -e "${GREEN}✅ Удаление завершено${NC}"
