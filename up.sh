#!/bin/bash
# Sobe o Flow com o Node isolado e os pacotes da ponte do WhatsApp.
set -euo pipefail
cd "$(dirname "$0")"

node_home="$HOME/Library/Application Support/Flow/node"
if [ ! -x "$node_home/bin/node" ]; then
  echo "Node isolado ausente. Rode ./node-install.sh"
  exit 1
fi

export PATH="$node_home/bin:$PATH"
(cd src/flow/baileys && npm install)
exec uv run python -m flow
