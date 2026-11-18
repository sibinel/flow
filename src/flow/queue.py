"""Listas da fila. A janela só as vê através do controller."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt


class RecordModel(QAbstractListModel):
    def __init__(self, roles: list[str]) -> None:
        super().__init__()
        self._role_ids = {
            int(Qt.ItemDataRole.UserRole) + 1 + index: name
            for index, name in enumerate(roles)
        }
        self._rows: list[dict[str, object]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        name = self._role_ids.get(role)
        if name is None:
            return None
        return self._rows[index.row()][name]

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {role: QByteArray(name.encode()) for role, name in self._role_ids.items()}

    def replace(self, rows: list[dict[str, object]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
