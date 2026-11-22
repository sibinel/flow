"""Fronteira única da janela. A janela não abre o banco e não envia."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QObject, Slot

from flow.classify import Classifier, ReadTool
from flow.mail import ImapReader
from flow.paths import ensure_user_data
from flow.queue import RecordModel
from flow.store import database_file, ensure_demo, open_database


class Controller(QObject):
    def __init__(self, data_root: Path | None = None) -> None:
        super().__init__()
        self._data_dir = ensure_user_data(data_root)
        self._db = open_database(self._data_dir)
        if data_root is None:
            ensure_demo(self._db)
        self._drafts = RecordModel(["draftId", "text"])
        self._pendings = RecordModel(["entryId", "text", "reason"])
        self._clients = RecordModel(["clientId", "name"])
        self._processes = RecordModel(["processId", "label"])
        for model in (self._drafts, self._pendings, self._clients, self._processes):
            model.setParent(self)
        self._reload()
        self.selectClient(0)

    @Property(str, constant=True)
    def dataDirectory(self) -> str:
        return str(self._data_dir)

    @property
    def database_path(self) -> Path:
        return database_file(self._data_dir)

    @Property(QObject, constant=True)
    def drafts(self) -> QObject:
        return self._drafts

    @Property(QObject, constant=True)
    def pendings(self) -> QObject:
        return self._pendings

    @Property(QObject, constant=True)
    def clients(self) -> QObject:
        return self._clients

    @Property(QObject, constant=True)
    def processes(self) -> QObject:
        return self._processes

    @Slot(int, result=bool)
    def approve(self, draft_id: int) -> bool:
        """Marca o rascunho como aprovado. Não cria envio."""
        changed = self._db.execute(
            """
            UPDATE rascunho SET estado = 'aprovado'
            WHERE id = ? AND estado = 'aguardando'
            """,
            (draft_id,),
        )
        if changed.rowcount != 1:
            self._db.rollback()
            return False
        self._db.commit()
        self._reload()
        return True

    @Slot(int, int, int, result=bool)
    def indicate(self, entry_id: int, client_id: int, process_id: int) -> bool:
        """Grava o cliente e, se couber, o processo. Tira a entrada de pendente."""
        if client_id <= 0 or not self._exists("cliente", client_id):
            return False
        pending = self._db.execute(
            "SELECT id FROM entrada WHERE id = ? AND estado = 'pendente'",
            (entry_id,),
        ).fetchone()
        if pending is None:
            return False
        process: int | None
        if process_id <= 0:
            process = None
        else:
            owner = self._db.execute(
                "SELECT cliente_id FROM processo WHERE id = ?",
                (process_id,),
            ).fetchone()
            if owner is None or owner["cliente_id"] != client_id:
                return False
            process = process_id
        changed = self._db.execute(
            """
            UPDATE entrada
            SET cliente_id = ?, processo_id = ?, estado = 'arquivada'
            WHERE id = ? AND estado = 'pendente'
            """,
            (client_id, process, entry_id),
        )
        if changed.rowcount != 1:
            self._db.rollback()
            return False
        self._db.commit()
        self._reload()
        return True

    @Slot(int)
    def selectClient(self, client_id: int) -> None:  # noqa: N802
        rows: list[dict[str, object]] = [{"processId": 0, "label": "sem processo"}]
        if client_id > 0:
            rows.extend(
                {
                    "processId": row["id"],
                    "label": f"{row['número']} — {row['título']}",
                }
                for row in self._db.execute(
                    """
                    SELECT id, "número", "título"
                    FROM processo WHERE cliente_id = ? ORDER BY id
                    """,
                    (client_id,),
                )
            )
        self._processes.replace(rows)

    def ingest_mail(self, host: str, port: int, user: str, password: str) -> list[int]:
        """Lê a caixa IMAP. Não classifica e não envia."""
        entry_ids = ImapReader(self._db, self._data_dir).ingest(host, port, user, password)
        self._db.commit()
        self._reload()
        return entry_ids

    def classify_entry(self, entry_id: int) -> str:
        """Classifica uma entrada. A janela não chama este método."""
        result = Classifier(ReadTool(self._db)).classify(entry_id)
        self._db.commit()
        self._reload()
        return result

    def close(self) -> None:
        self._db.close()

    def _exists(self, table: str, row_id: int) -> bool:
        if table not in {"cliente", "processo", "entrada", "rascunho"}:
            return False
        row = self._db.execute(
            f"SELECT 1 FROM {table} WHERE id = ?",
            (row_id,),
        ).fetchone()
        return row is not None

    def _reload(self) -> None:
        self._drafts.replace(
            [
                {"draftId": row["id"], "text": row["texto"]}
                for row in self._db.execute(
                    """
                    SELECT id, texto FROM rascunho
                    WHERE estado = 'aguardando' ORDER BY id
                    """
                )
            ]
        )
        self._pendings.replace(
            [
                {
                    "entryId": row["id"],
                    "text": row["texto"],
                    "reason": row["motivo"] or "",
                }
                for row in self._db.execute(
                    """
                    SELECT entrada.id, entrada.texto, pendencia.motivo
                    FROM entrada
                    LEFT JOIN pendencia ON pendencia.id = (
                        SELECT id FROM pendencia
                        WHERE entrada_id = entrada.id ORDER BY id LIMIT 1
                    )
                    WHERE entrada.estado = 'pendente'
                    ORDER BY entrada.id
                    """
                )
            ]
        )
        self._clients.replace(
            [
                {"clientId": row["id"], "name": row["nome"]}
                for row in self._db.execute("SELECT id, nome FROM cliente ORDER BY nome, id")
            ]
        )
