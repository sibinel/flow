#!/bin/sh
# O .dmg é alvo do pacote. Se o hdiutil falhar, o .app continua valendo.
set -u
cd "$(dirname "$0")/.."
APP="dist/Flow.app"
DMG="dist/Flow.dmg"
if [ ! -d "$APP" ]; then
  echo "Flow.app ausente; dmg não gerado."
  exit 0
fi
rm -f "$DMG"
if hdiutil create -volname Flow -srcfolder "$APP" -ov -format UDZO "$DMG"; then
  echo "dmg: $DMG"
else
  echo "hdiutil falhou; a etapa segue com $APP"
fi
exit 0
