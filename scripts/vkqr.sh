#!/bin/sh
# vkqr — один скрипт для Linux/macOS: ставит Python (при необходимости),
# git, pipx и запускает утилиту vkqr.
#
# Скачать и запустить (не через `| sh`, иначе stdin занят текстом скрипта):
#   curl -fsSL https://raw.githubusercontent.com/matrixd0t/vkqr/master/scripts/vkqr.sh -o vkqr.sh
#   sh vkqr.sh -o cookies.json
set -eu

REPO_URL="${VKQR_REPO:-https://github.com/matrixd0t/vkqr.git}"

log() { printf 'vkqr: %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

ensure_python() {
    if have python3; then return 0; fi
    log "python3 не найден — пробую установить..."
    if have apt-get; then
        as_root apt-get update
        as_root apt-get install -y python3 python3-venv python3-pip
    elif have dnf; then
        as_root dnf install -y python3 python3-pip
    elif have yum; then
        as_root yum install -y python3 python3-pip
    elif have zypper; then
        as_root zypper --non-interactive install python3 python3-pip
    elif have pacman; then
        as_root pacman -Sy --noconfirm python python-pip
    elif have apk; then
        as_root apk add --no-cache python3 py3-pip
    elif have brew; then
        brew install python
    else
        log "Не удалось установить Python автоматически. Установите Python 3.9+ вручную."
        exit 1
    fi
    have python3 || { log "python3 всё ещё не найден в PATH"; exit 1; }
    python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 9):
    raise SystemExit("vkqr: требуется Python 3.9+, найден %d.%d" % sys.version_info[:2])
PY
}

ensure_git() {
    have git && return 0
    log "git не найден — пробую установить (нужен pipx для установки с GitHub)..."
    if have apt-get; then as_root apt-get install -y git
    elif have dnf; then as_root dnf install -y git
    elif have yum; then as_root yum install -y git
    elif have zypper; then as_root zypper --non-interactive install git
    elif have pacman; then as_root pacman -Sy --noconfirm git
    elif have apk; then as_root apk add --no-cache git
    elif have brew; then brew install git
    fi
    have git || { log "git не найден. Установите git и повторите запуск."; exit 1; }
}

ensure_pipx() {
    have pipx && return 0
    log "pipx не найден — устанавливаю..."
    if have apt-get; then as_root apt-get install -y pipx
    elif have dnf; then as_root dnf install -y pipx
    elif have yum; then as_root yum install -y pipx
    elif have zypper; then as_root zypper --non-interactive install python3-pipx
    elif have pacman; then as_root pacman -Sy --noconfirm python-pipx
    elif have apk; then as_root apk add --no-cache py3-pipx
    fi
    if ! have pipx; then
        python3 -m pip install --user --upgrade pipx 2>/dev/null \
            || python3 -m pip install --user --upgrade pipx --break-system-packages 2>/dev/null \
            || true
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    fi
    have pipx || { log "Не удалось установить pipx. Установите pipx вручную."; exit 1; }
}

if have vkqr; then
    exec vkqr "$@"
fi

ensure_python
ensure_git
ensure_pipx

LATEST_COMMIT="$(git ls-remote "$REPO_URL" HEAD | cut -f1)"
[ -n "$LATEST_COMMIT" ] || { log "Не удалось получить актуальный commit из ${REPO_URL}"; exit 1; }

exec pipx run --spec "git+${REPO_URL}@${LATEST_COMMIT}" vkqr "$@"
