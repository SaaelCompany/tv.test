#!/usr/bin/env bash
# Совместимость: раньше этот файл только копировал себя в /root.
# Теперь он запускает автоустановку.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/install.sh" "$@"
