"""Fronteira única da janela. A janela não abre o banco."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QObject

from flow.paths import ensure_user_data
from flow.store import database_file, open_database


class Controller(QObject):
    def __init__(self, data_root: Path | None = None) -> None:
        super().__init__()
        self._data_dir = ensure_user_data(data_root)
        self._db = open_database(self._data_dir)

    @Property(str, constant=True)
    def dataDirectory(self) -> str:
        return str(self._data_dir)

    @property
    def database_path(self) -> Path:
        return database_file(self._data_dir)

    def close(self) -> None:
        self._db.close()
