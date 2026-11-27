"""Fronteira única da janela. A janela não abre o banco e não envia."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QCoreApplication, QObject, QTimer, Signal, Slot

from flow.classify import Classifier, ReadTool
from flow.cycle import DayCycle
from flow.harness import Harness
from flow.llm import CloudLLM, load_model_key
from flow.notice import clear_notice, publish_notice, read_notice
from flow.mail import ImapReader, load_mail_account
from flow.paths import ensure_user_data, whatsapp_session
from flow.send import SmtpSender, load_smtp_target
from flow.whatsapp import WhatsAppReader, WhatsAppSender, commit_cursor, pair, session_ready
from flow.queue import RecordModel
from flow.store import database_file, open_database

_STOPPED = "Leitura e envio de WhatsApp estão parados."


class Controller(QObject):
    backgroundDone = Signal()
    surfaceChanged = Signal()

    def __init__(self, data_root: Path | None = None) -> None:
        super().__init__()
        self._data_dir = ensure_user_data(data_root)
        self._db = open_database(self._data_dir)
        self._qr_image = ""
        self._notice = ""
        self._session_ready = False
        self._clock: QTimer | None = None
        self._qr_clock: QTimer | None = None
        self._pairing = False
        self._cycle_thread: threading.Thread | None = None
        self.backgroundDone.connect(self._reload)
        self._drafts = RecordModel(["draftId", "text", "address", "channel", "state"])
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
        self._clock.timeout.connect(self._start_background_cycle)
        self._clock.start()
        self._qr_clock = QTimer(self)
        self._qr_clock.setInterval(400)
        self._qr_clock.timeout.connect(self._watch_qr)
        self._qr_clock.start()

    def _tick(self, now: datetime | None = None):
        moment = datetime.now().astimezone() if now is None else now
        return self.run_cycle(moment, self._dispatch_approved)

    def _start_background_cycle(self) -> None:
        """O relógio da janela não espera a rede."""
        if self._cycle_thread is not None and self._cycle_thread.is_alive():
            return
        self._cycle_thread = threading.Thread(target=self._run_background_cycle, daemon=True)
        self._cycle_thread.start()

    def _run_background_cycle(self) -> None:
        db = open_database(self._data_dir)
        try:
            try:
                DayCycle(db, self._data_dir).run(
                    datetime.now().astimezone(),
                    lambda draft_id, channel: self._dispatch_approved(draft_id, channel, db),
                    lambda: self._collect(db),
                )
            except Exception:
                db.rollback()
        finally:
            db.close()
            self.backgroundDone.emit()

    def _collect(self, connection=None) -> bool:
        """Coleta antes da fila. Um lado que falha não impede o outro."""
        db = self._db if connection is None else connection
        ok = True
        account = load_mail_account(self._data_dir)
        if account is not None:
            try:
                ImapReader(db, self._data_dir).ingest(
                    account.imap_host,
                    account.imap_port,
                    account.user,
                    account.password,
                )
            except Exception:
                ok = False
        try:
            if session_ready(whatsapp_session(self._data_dir)):
                WhatsAppReader(db, self._data_dir).ingest()
        except Exception:
            ok = False
        return ok

    def _dispatch_approved(self, draft_id: int, channel: str, connection=None) -> bool:
        db = self._db if connection is None else connection
        if channel == "email":
            account = load_mail_account(self._data_dir)
            target = load_smtp_target(self._data_dir)
            if account is None or target is None:
                return False
            host, port = target
            return SmtpSender(db).send(
                draft_id,
                host,
                port,
                account.user,
                account.password,
            )
        if channel == "whatsapp":
            if not session_ready(whatsapp_session(self._data_dir)):
                return False
            return WhatsAppSender(db, self._data_dir).send(draft_id)
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

    @Property(str, notify=surfaceChanged)
    def notice(self) -> str:
        return self._notice

    @Slot(int, result=bool)
    def approve(self, draft_id: int) -> bool:
        """Marca o rascunho como aprovado e congela texto, destino e clientes."""
        row = self._db.execute(
            """
            SELECT rascunho.texto, contato.valor, contato.tipo,
                   entrada.cliente_id AS entrada_cliente,
                   contato.cliente_id AS contato_cliente
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.id = ? AND rascunho.estado = 'aguardando'
            """,
            (draft_id,),
        ).fetchone()
        if row is None:
            self._db.rollback()
            return False
        changed = self._db.execute(
            """
            UPDATE rascunho
            SET estado = 'aprovado',
                texto_aprovado = ?,
                destino_aprovado = ?,
                canal_aprovado = ?,
                cliente_entrada_aprovado = ?,
                cliente_contato_aprovado = ?
            WHERE id = ? AND estado = 'aguardando'
            """,
            (
                row["texto"],
                row["valor"],
                row["tipo"],
                row["entrada_cliente"],
                row["contato_cliente"],
                draft_id,
            ),
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
        try:
            sent = sender_tool.send(draft_id, host, port, sender, password)
        except Exception:
            self._reload()
            raise
        self._db.commit()
        self._reload()
        return sent

    def ingest_whatsapp(self, runner=None) -> list[int]:
        """Lê o WhatsApp. Sem sessão gravada, para até um QR novo. Não envia."""
        entry_ids = WhatsAppReader(self._db, self._data_dir, runner).ingest()
        self._db.commit()
        commit_cursor(self._db)
        self._reload()
        return entry_ids

    def send_approved_whatsapp(self, draft_id: int, runner=None) -> bool:
        """Envia só rascunho aprovado. Sem sessão, a tool de envio não entra."""
        sender = WhatsAppSender(self._db, self._data_dir, runner)
        try:
            sent = sender.send(draft_id)
        except Exception:
            self._reload()
            raise
        self._db.commit()
        self._reload()
        return sent

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
        return CloudLLM(
            self._db,
            transport=transport,
            api_key=load_model_key(self._data_dir),
        ).complete(
            task,
            entry_id=entry_id,
            client_id=client_id,
            message=message,
            query=query,
        )

    @Slot(str, result=int)
    def search(self, phrase: str, transport=None) -> int:
        """Busca trechos do cliente citado e grava o rascunho. Não envia."""
        key = load_model_key(self._data_dir)
        if transport is not None:
            try:
                hit = Harness(
                    self._db,
                    CloudLLM(self._db, transport=transport, api_key=key),
                ).search(phrase)
            except Exception as exc:
                publish_notice(self._data_dir, str(exc))
                self._reload()
                return 0
            clear_notice(self._data_dir)
            self._reload()
            return hit.draft_id
        threading.Thread(target=self._search_away, args=(phrase,), daemon=True).start()
        return 0

    def _search_away(self, phrase: str) -> None:
        db = open_database(self._data_dir)
        try:
            try:
                Harness(
                    db,
                    CloudLLM(db, api_key=load_model_key(self._data_dir)),
                ).search(phrase)
            except Exception as exc:
                db.rollback()
                publish_notice(self._data_dir, str(exc))
            else:
                clear_notice(self._data_dir)
        finally:
            db.close()
            self.backgroundDone.emit()

    def run_cycle(self, now, dispatch=None, config=None):
        """Monta a fila no horário e envia o que já foi aprovado. Fora dele, não envia."""
        try:
            queue = DayCycle(self._db, self._data_dir, config).run(now, dispatch, self._collect)
        except Exception:
            self._reload()
            raise
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
        self._notice = read_notice(self._data_dir)
        self.surfaceChanged.emit()
        self._drafts.replace(
            [
                {
                    "draftId": row["id"],
                    "text": row["texto"],
                    "address": row["valor"],
                    "channel": row["tipo"],
                    "state": row["estado"],
                }
                for row in self._db.execute(
                    """
                    SELECT rascunho.id,
                           COALESCE(rascunho.texto_aprovado, rascunho.texto) AS texto,
                           rascunho.estado,
                           COALESCE(rascunho.destino_aprovado, contato.valor) AS valor,
                           COALESCE(rascunho.canal_aprovado, contato.tipo) AS tipo
                    FROM rascunho
                    JOIN entrada ON entrada.id = rascunho.entrada_id
                    JOIN contato ON contato.id = entrada.contato_id
                    WHERE rascunho.estado IN ('aguardando', 'em curso')
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
