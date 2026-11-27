#!/bin/bash
# Instala o Node LTS numa pasta só do Flow, sem instalador e sem Homebrew.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Este script é para macOS."
  exit 1
fi

node_home="$HOME/Library/Application Support/Flow/node"
cpu="$(uname -m)"
case "$cpu" in
  arm64) cpu="arm64" ;;
  x86_64) cpu="x64" ;;
  *)
    echo "Arquitetura não suportada: $cpu"
    exit 1
    ;;
esac

version="$(
  curl -fsSL https://nodejs.org/dist/index.json \
    | python3 -c 'import json,sys; print(next(item["version"] for item in json.load(sys.stdin) if item["lts"]))'
)"

if [ -x "$node_home/bin/node" ] && [ "$("$node_home/bin/node" -v)" = "$version" ]; then
  echo "Node $version já está em $node_home"
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -fsSL -o "$work/node.tar.gz" "https://nodejs.org/dist/${version}/node-${version}-darwin-${cpu}.tar.gz"
mkdir "$work/extracted"
tar -xzf "$work/node.tar.gz" -C "$work/extracted" --strip-components=1
mkdir -p "$(dirname "$node_home")"
rm -rf "$node_home"
mv "$work/extracted" "$node_home"
echo "Node $version instalado em $node_home"
