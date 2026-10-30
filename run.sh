#!/bin/bash
# HR-помощник: при первом запуске ставит библиотеки, затем запускает программу и открывает браузер.
cd "$(dirname "$0")" || exit 1
URL="http://localhost:8501"

open_browser() {
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1
    elif command -v open >/dev/null 2>&1; then
        open "$URL"
    else
        echo "Откройте в браузере: $URL"
    fi
}

download_get_pip() {
    local url="https://bootstrap.pypa.io/get-pip.py" out=".venv/get-pip.py"
    for _ in 1 2 3; do
        if command -v curl >/dev/null 2>&1 && curl -fsSL "$url" -o "$out"; then return 0; fi
        if command -v wget >/dev/null 2>&1 && wget -q "$url" -O "$out"; then return 0; fi
        if python3 -c "import urllib.request; urllib.request.urlretrieve('$url', '$out')" 2>/dev/null; then return 0; fi
        sleep 2
    done
    return 1
}

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Нужен Python 3.10 или новее. Сейчас: $(python3 --version 2>&1)"
    exit 1
fi

if [ ! -x .venv/bin/streamlit ]; then
    echo "Первый запуск: ставлю библиотеки, это 1-3 минуты..."
    if ! python3 -m venv .venv >/dev/null 2>&1; then
        echo "В системе нет python3-venv — ставлю pip отдельно (нужен интернет)..."
        rm -rf .venv
        if ! python3 -m venv --without-pip .venv || ! download_get_pip || ! .venv/bin/python .venv/get-pip.py -q; then
            echo "Не получилось создать окружение. Попробуйте: sudo apt install python3-venv  (или python3.X-venv)"
            rm -rf .venv
            exit 1
        fi
    fi
    .venv/bin/pip install -q --upgrade pip
    if ! .venv/bin/pip install -q -r requirements.txt; then
        echo "Не получилось установить библиотеки — проверьте интернет."
        rm -rf .venv
        exit 1
    fi
fi

if [ ! -f .env ]; then
    cp .env.example .env
fi
if ! grep -q '^GIGACHAT_AUTH_KEY=..' .env && [ -t 0 ]; then
    echo
    echo "Вставьте ключ GigaChat (Ctrl+Shift+V) и нажмите Enter."
    echo "Просто Enter — запуск в демо-режиме без GigaChat."
    read -r KEY
    KEY="$(echo "$KEY" | tr -d '[:space:]')"
    if [ -n "$KEY" ]; then
        grep -v '^GIGACHAT_AUTH_KEY=' .env > .env.tmp
        echo "GIGACHAT_AUTH_KEY=$KEY" >> .env.tmp
        mv .env.tmp .env
        chmod 600 .env
        echo "Ключ сохранён в .env (в git он не попадает)."
    fi
fi

if .venv/bin/python -c "import urllib.request; urllib.request.urlopen('$URL', timeout=2)" 2>/dev/null; then
    echo "Программа уже запущена — открываю браузер."
    open_browser
    exit 0
fi

(
    for _ in $(seq 1 60); do
        sleep 1
        if .venv/bin/python -c "import urllib.request; urllib.request.urlopen('$URL', timeout=2)" 2>/dev/null; then
            open_browser
            break
        fi
    done
) &

echo "Запускаю HR-помощника: $URL (остановить — Ctrl+C)"
exec .venv/bin/streamlit run app.py --server.port 8501
