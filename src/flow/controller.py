"""Fronteira única da janela. Sem modelo, banco ou canal."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QObject

from flow.paths import ensure_user_data


class Controller(QObject):
    def __init__(self, data_root: Path | None = None) -> None:
        super().__init__()
        self._data_dir = ensure_user_data(data_root)

    @Property(str, constant=True)
    def dataDirectory(self) -> str:
        return str(self._data_dir)
