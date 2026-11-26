"""Fronteira única da janela. A janela não abre o banco e não envia."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QCoreApplication, QObject, QTimer, Signal, Slot

from flow.classify import Classifier, ReadTool
from flow.cycle import DayCycle
from flow.harness import Harness
from flow.llm import CloudLLM
from flow.mail import ImapReader, load_mail_account
from flow.paths import ensure_user_data, whatsapp_session
from flow.send import SmtpSender, load_smtp_target
from flow.whatsapp import WhatsAppReader, WhatsAppSender, pair, session_ready
from flow.queue import RecordModel
from flow.store import database_file, ensure_demo, open_database

_STOPPED = "Leitura e envio de WhatsApp estão parados."


class Controller(QObject):
    surfaceChanged = Signal()

    def __init__(self, data_root: Path | None = None) -> None:
        super().__init__()
        self._data_dir = ensure_user_data(data_root)
        self._db = open_database(self._data_dir)
        self._qr_image = ""
        self._session_ready = False
        self._clock: QTimer | None = None
        self._qr_clock: QTimer | None = None
        self._pairing = False
        if data_root is None:
            ensure_demo(self._db)
        self._drafts = RecordModel(["draftId", "text", "address", "channel"])
        self._pendings = RecordModel(["entryId", "text", "reason"])
        self._clients = RecordModel(["clientId", "name"])
        self._processes = RecordModel(["processId", "label"])
        for model in (self._drafts, self._pendings, self._clients, self._processes):
            model.setParent(self)
        self._reload()
        self.selectClient(0)
        try:
            self._session_ready = session_ready(whatsapp_session(self._data_dir))
        except ValueError:
            self._session_ready = False

    def watch_schedule(self) -> None:
        """A partir da abertura, olha o horário e chama o ciclo só nele."""
        if QCoreApplication.instance() is None or self._clock is not None:
            return
        self._clock = QTimer(self)
        self._clock.setInterval(15000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self._qr_clock = QTimer(self)
        self._qr_clock.setInterval(400)
        self._qr_clock.timeout.connect(self._watch_qr)
        self._qr_clock.start()

    def _tick(self, now: datetime | None = None):
        moment = datetime.now().astimezone() if now is None else now
        return self.run_cycle(moment, self._dispatch_approved)

    def _collect(self) -> None:
        """Coleta antes da fila. Sem conta ou sem sessão, esse lado não corre."""
        account = load_mail_account(self._data_dir)
        if account is not None:
            ImapReader(self._db, self._data_dir).ingest(
                account.imap_host,
                account.imap_port,
                account.user,
                account.password,
            )
        if session_ready(whatsapp_session(self._data_dir)):
            WhatsAppReader(self._db, self._data_dir).ingest()

    def _dispatch_approved(self, draft_id: int, channel: str) -> bool:
        if channel == "email":
            account = load_mail_account(self._data_dir)
            target = load_smtp_target(self._data_dir)
            if account is None or target is None:
                return False
            host, port = target
            return SmtpSender(self._db).send(
                draft_id,
                host,
                port,
                account.user,
                account.password,
            )
        if channel == "whatsapp":
            if not session_ready(whatsapp_session(self._data_dir)):
                return False
            return WhatsAppSender(self._db, self._data_dir).send(draft_id)
        return False

    def _watch_qr(self) -> None:
        try:
            session = whatsapp_session(self._data_dir)
        except ValueError:
            return
        ready = session_ready(session)
        image = session / "qr.png"
        uri = image.resolve().as_uri() if image.is_file() else ""
        if uri == self._qr_image and ready == self._session_ready:
            return
        self._qr_image = uri
        self._session_ready = ready
        self.surfaceChanged.emit()

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

    @Property(str, notify=surfaceChanged)
    def whatsappNotice(self) -> str:
        if self._session_ready:
            return ""
        return _STOPPED

    @Property(str, notify=surfaceChanged)
    def qrImage(self) -> str:
        return self._qr_image

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

    def send_approved_mail(
        self, draft_id: int, host: str, port: int, sender: str, password: str
    ) -> bool:
        """Envia só rascunho aprovado. Sem aprovação, a tool de envio não entra."""
        sender_tool = SmtpSender(self._db)
        if not sender_tool.send(draft_id, host, port, sender, password):
            self._db.commit()
            self._reload()
            return False
        self._db.commit()
        self._reload()
        return True

    def ingest_whatsapp(self, runner=None) -> list[int]:
        """Lê o WhatsApp. Sem sessão gravada, para até um QR novo. Não envia."""
        entry_ids = WhatsAppReader(self._db, self._data_dir, runner).ingest()
        self._db.commit()
        self._reload()
        return entry_ids

    def send_approved_whatsapp(self, draft_id: int, runner=None) -> bool:
        """Envia só rascunho aprovado. Sem sessão, a tool de envio não entra."""
        sender = WhatsAppSender(self._db, self._data_dir, runner)
        if not sender.send(draft_id):
            self._db.commit()
            self._reload()
            return False
        self._db.commit()
        self._reload()
        return True

    @Slot()
    def pair_whatsapp(self, runner=None) -> None:
        """Pede o QR e grava a sessão fora da pasta de documentos."""
        if runner is not None:
            pair(self._data_dir, runner)
            self._watch_qr()
            return
        if self._pairing:
            return
        self._pairing = True

        def work() -> None:
            try:
                pair(self._data_dir, None)
            finally:
                self._pairing = False

        threading.Thread(target=work, daemon=True).start()

    def classify_entry(self, entry_id: int) -> str:
        """Classifica uma entrada. A janela não chama este método."""
        result = Classifier(ReadTool(self._db)).classify(entry_id)
        self._db.commit()
        self._reload()
        return result

    def complete_model(
        self,
        task: str,
        entry_id: int = 0,
        client_id: int = 0,
        message: str = "",
        query: str = "",
        transport=None,
    ) -> str:
        """Chama o modelo com o pedaço da tarefa. Não envia."""
        return CloudLLM(self._db, transport=transport).complete(
            task,
            entry_id=entry_id,
            client_id=client_id,
            message=message,
            query=query,
        )

    @Slot(str, result=int)
    def search(self, phrase: str, transport=None) -> int:
        """Busca trechos do cliente citado e grava o rascunho. Não envia."""
        hit = Harness(self._db, CloudLLM(self._db, transport=transport)).search(phrase)
        self._reload()
        return hit.draft_id

    def run_cycle(self, now, dispatch=None, config=None):
        """Monta a fila no horário e envia o que já foi aprovado. Fora dele, não envia."""
        queue = DayCycle(self._db, self._data_dir, config).run(now, dispatch, self._collect)
        if queue.ran:
            self._reload()
        return queue

    def close(self) -> None:
        if self._clock is not None:
            self._clock.stop()
        if self._qr_clock is not None:
            self._qr_clock.stop()
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
                {
                    "draftId": row["id"],
                    "text": row["texto"],
                    "address": row["valor"],
                    "channel": row["tipo"],
                }
                for row in self._db.execute(
                    """
                    SELECT rascunho.id, rascunho.texto, contato.valor, contato.tipo
                    FROM rascunho
                    JOIN entrada ON entrada.id = rascunho.entrada_id
                    JOIN contato ON contato.id = entrada.contato_id
                    WHERE rascunho.estado = 'aguardando'
                    ORDER BY rascunho.id
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
