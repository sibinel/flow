#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
uv run python -c '
from flow.paths import ensure_user_data
from flow.store import ensure_demo, open_database

connection = open_database(ensure_user_data())
try:
    ensure_demo(connection)
finally:
    connection.close()
'
echo "Fila recarregada."
