#!/bin/bash
# Открывает презентацию в браузере. Листать: стрелки или пробел, полный экран: F.
cd "$(dirname "$0")" || exit 1
FILE="$(pwd)/presentation/slides.html"
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$FILE" >/dev/null 2>&1
elif command -v open >/dev/null 2>&1; then
    open "$FILE"
else
    echo "Откройте в браузере: $FILE"
fi
