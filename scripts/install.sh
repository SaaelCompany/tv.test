#!/usr/bin/env bash
# Совместимость со старым путём. Вся установка — в корневом install.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/install.sh" "$@"
