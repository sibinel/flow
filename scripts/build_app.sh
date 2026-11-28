#!/bin/bash
# Gera o Flow.app com o Node e os pacotes da ponte do WhatsApp dentro, e depois o .dmg.
set -euo pipefail
cd "$(dirname "$0")/.."

runtime="$PWD/build/node-runtime"
./node-install.sh "$runtime"
export PATH="$runtime/bin:$PATH"
(cd src/flow/baileys && npm ci --omit=dev)
uv run --extra dev pyinstaller flow.spec --noconfirm
./scripts/make_dmg.sh
