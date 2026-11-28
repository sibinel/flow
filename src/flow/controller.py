"""Fronteira única da janela. A janela não abre o banco e não envia."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QCoreApplication, QObject, Qt, QTimer, Signal, Slot

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
from flow.store import add_entry_trecho, database_file, open_database

_STOPPED = "Leitura e envio de WhatsApp estão parados."


def _approval_fingerprint(row) -> str:
    values = (
        str(row["text"] or ""),
        str(row["address"] or ""),
        str(row["channel"] or ""),
        str(row["entry_client"] or "sem cliente"),
        str(row["contact_client"] or "sem cliente"),
        row["entry_client_id"],
        row["contact_client_id"],
    )
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Controller(QObject):
    backgroundDone = Signal()
    surfaceChanged = Signal()
    searchChoiceReady = Signal(int, object)

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
        self._search_generation = 0
        self.backgroundDone.connect(self._reload)
        self.searchChoiceReady.connect(self._show_search_choice, Qt.ConnectionType.QueuedConnection)
        self._drafts = RecordModel(
            [
                "draftId",
                "text",
                "address",
                "channel",
                "entryClient",
                "contactClient",
                "clientsMatch",
                "approvalVersion",
                "state",
            ]
        )
        self._pendings = RecordModel(["entryId", "text", "reason", "unowned"])
        self._clients = RecordModel(["clientId", "name"])
        self._processes = RecordModel(["processId", "label"])
        self._search_candidates = RecordModel(["clientId", "name"])
        for model in (self._drafts, self._pendings, self._clients, self._processes, self._search_candidates):
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
            except Exception as exc:
                if db.in_transaction:
                    db.rollback()
                publish_notice(self._data_dir, f"O ciclo falhou: {exc}")
        finally:
            db.close()
            self.backgroundDone.emit()

    def _collect(self, connection=None) -> bool:
        """Coleta antes da fila. Um lado que falha não impede o outro."""
        db = self._db if connection is None else connection
        ok = True
        errors: list[str] = []
        account = load_mail_account(self._data_dir)
        if account is not None:
            try:
                result = ImapReader(db, self._data_dir).ingest(
                    account.imap_host,
                    account.imap_port,
                    account.user,
                    account.password,
                )
                if getattr(result, "failures", []):
                    ok = False
                    errors.append(f"E-mail: {len(result.failures)} mensagem(ns) não gravada(s).")
            except Exception as exc:
                ok = False
                errors.append(f"E-mail: falha na coleta ({exc}).")
        try:
            if session_ready(whatsapp_session(self._data_dir)):
                result = WhatsAppReader(db, self._data_dir).ingest()
                if getattr(result, "failures", []):
                    ok = False
                    errors.append(f"WhatsApp: {len(result.failures)} mensagem(ns) não gravada(s).")
                if getattr(result, "warnings", []):
                    errors.append("WhatsApp: " + "; ".join(dict.fromkeys(result.warnings)))
        except Exception as exc:
            ok = False
            errors.append(f"WhatsApp: falha na coleta ({exc}).")
        if errors:
            publish_notice(self._data_dir, " ".join(errors))
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

    @Property(QObject, constant=True)
    def searchCandidates(self) -> QObject:
        return self._search_candidates

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

    @Slot(int, str, result=bool)
    def approve(self, draft_id: int, displayed_version: str) -> bool:
        """Aprova somente a mesma versão íntegra e visível na fila."""
        try:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                """
                SELECT rascunho.texto AS text, contato.valor AS address,
                       contato.tipo AS channel,
                       entrada.cliente_id AS entry_client_id,
                       contato.cliente_id AS contact_client_id,
                       entry_client.nome AS entry_client,
                       contact_client.nome AS contact_client
                FROM rascunho
                JOIN entrada ON entrada.id = rascunho.entrada_id
                JOIN contato ON contato.id = entrada.contato_id
                LEFT JOIN cliente AS entry_client ON entry_client.id = entrada.cliente_id
                LEFT JOIN cliente AS contact_client ON contact_client.id = contato.cliente_id
                WHERE rascunho.id = ? AND rascunho.estado = 'aguardando'
                """,
                (draft_id,),
            ).fetchone()
            if row is None:
                self._db.rollback()
                self._reload()
                return False
            current_version = _approval_fingerprint(row)
            if not hmac.compare_digest(current_version, str(displayed_version)):
                self._db.rollback()
                publish_notice(self._data_dir, "Os dados do rascunho mudaram; revise a versão atual.")
                self._reload()
                return False
            if (
                row["entry_client_id"] is None
                or row["entry_client_id"] != row["contact_client_id"]
            ):
                self._db.rollback()
                publish_notice(self._data_dir, "Os clientes da entrada e do contato divergem.")
                self._reload()
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
                    row["text"],
                    row["address"],
                    row["channel"],
                    row["entry_client_id"],
                    row["contact_client_id"],
                    draft_id,
                ),
            )
            if changed.rowcount != 1:
                self._db.rollback()
                self._reload()
                return False
            self._db.commit()
        except sqlite3.OperationalError as exc:
            if self._db.in_transaction:
                self._db.rollback()
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            publish_notice(self._data_dir, "O banco está ocupado; tente aprovar de novo.")
            self._reload()
            return False
        except Exception:
            if self._db.in_transaction:
                self._db.rollback()
            raise
        clear_notice(self._data_dir)
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
        # Contato sem dono passa a ser do cliente indicado. Contato com dono não muda.
        self._db.execute(
            """
            UPDATE contato SET cliente_id = ?
            WHERE cliente_id IS NULL
              AND id = (SELECT contato_id FROM entrada WHERE id = ?)
            """,
            (client_id, entry_id),
        )
        add_entry_trecho(self._db, entry_id)
        self._db.commit()
        self._reload()
        return True

    @Slot(str, str, result=int)
    def createClient(self, name: str, status: str) -> int:  # noqa: N802
        """Cria cliente ou prospecção. Devolve o id, ou 0 se o dado não serve."""
        nome = " ".join(str(name or "").split())
        if not nome or status not in {"cliente", "prospecção"}:
            return 0
        created = self._db.execute(
            "INSERT INTO cliente (nome, status) VALUES (?, ?)",
            (nome, status),
        ).lastrowid
        self._db.commit()
        self._reload()
        return int(created)

    @Slot(int, result=bool)
    def markPersonal(self, entry_id: int) -> bool:  # noqa: N802
        """Marca o contato sem dono como pessoal e apaga as entradas pendentes dele."""
        docs = (self._data_dir / "documentos").resolve()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute(
                """
                SELECT contato.id AS contact_id, contato.cliente_id
                FROM entrada
                JOIN contato ON contato.id = entrada.contato_id
                WHERE entrada.id = ? AND entrada.estado = 'pendente'
                """,
                (entry_id,),
            ).fetchone()
            if row is None or row["cliente_id"] is not None:
                self._db.rollback()
                return False
            contact_id = int(row["contact_id"])
            entries = [
                int(item["id"])
                for item in self._db.execute(
                    "SELECT id FROM entrada WHERE contato_id = ? AND estado = 'pendente'",
                    (contact_id,),
                )
            ]
            marks = ",".join("?" for _ in entries)
            blocked = self._db.execute(
                f"""
                SELECT 1 FROM rascunho
                WHERE entrada_id IN ({marks})
                  AND (estado != 'aguardando'
                       OR EXISTS (SELECT 1 FROM envio WHERE envio.rascunho_id = rascunho.id))
                LIMIT 1
                """,
                entries,
            ).fetchone()
            if blocked is not None:
                self._db.rollback()
                publish_notice(
                    self._data_dir,
                    "Esse contato tem rascunho aprovado ou enviado; não foi marcado como pessoal.",
                )
                self._reload()
                return False
            paths = [
                str(item["caminho"])
                for item in self._db.execute(
                    f"SELECT caminho FROM documento WHERE entrada_id IN ({marks})",
                    entries,
                )
            ]
            for table in ("rascunho", "trecho", "documento", "pendencia"):
                self._db.execute(
                    f"DELETE FROM {table} WHERE entrada_id IN ({marks})",
                    entries,
                )
            self._db.execute(f"DELETE FROM entrada WHERE id IN ({marks})", entries)
            self._db.execute("UPDATE contato SET pessoal = 1 WHERE id = ?", (contact_id,))
            self._db.commit()
        except Exception:
            if self._db.in_transaction:
                self._db.rollback()
            raise
        for raw in paths:
            path = Path(raw).resolve()
            if docs in path.parents:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
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
        if entry_ids.failures:
            publish_notice(self._data_dir, f"E-mail: {len(entry_ids.failures)} mensagem(ns) não gravada(s).")
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
        if entry_ids.failures:
            publish_notice(self._data_dir, f"WhatsApp: {len(entry_ids.failures)} mensagem(ns) não gravada(s).")
        elif entry_ids.warnings:
            publish_notice(self._data_dir, "WhatsApp: " + "; ".join(dict.fromkeys(entry_ids.warnings)))
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

    @Slot()
    def clearSearchCandidates(self) -> None:
        self._search_generation += 1
        self._search_candidates.replace([])

    @Slot(str, int, result=int)
    def selectSearchCandidate(self, phrase: str, client_id: int) -> int:
        return self.search(phrase, client_id=client_id)

    @Slot(int, object)
    def _show_search_choice(self, generation: int, candidates) -> None:
        if generation != self._search_generation:
            return
        self._search_candidates.replace(
            [{"clientId": candidate.client_id, "name": candidate.name} for candidate in candidates]
        )

    @Slot(str, result=int)
    def search(self, phrase: str, transport=None, *, client_id: int = 0) -> int:
        """Busca trechos do cliente citado e grava o rascunho. Não envia."""
        self.clearSearchCandidates()
        generation = self._search_generation
        key = load_model_key(self._data_dir)
        if transport is not None:
            try:
                hit = Harness(
                    self._db,
                    CloudLLM(self._db, transport=transport, api_key=key),
                ).search(phrase, client_id=client_id)
            except Exception as exc:
                publish_notice(self._data_dir, str(exc))
                self._reload()
                return 0
            candidates = getattr(hit, "candidates", None)
            if candidates is not None:
                publish_notice(self._data_dir, "cliente ambíguo")
                self._reload()
                self._show_search_choice(generation, candidates)
                return 0
            clear_notice(self._data_dir)
            self._reload()
            return hit.draft_id
        if not client_id:
            try:
                candidates = Harness(self._db, CloudLLM(self._db, api_key=key)).candidates(phrase)
            except Exception:
                # Deixe a busca em segundo plano relatar o erro normalmente.
                pass
            else:
                if len(candidates) > 1:
                    publish_notice(self._data_dir, "cliente ambíguo")
                    self._reload()
                    self._show_search_choice(generation, candidates)
                    return 0
        threading.Thread(
            target=self._search_away, args=(phrase, client_id, generation), daemon=True
        ).start()
        return 0

    def _search_away(self, phrase: str, client_id: int, generation: int) -> None:
        db = open_database(self._data_dir)
        try:
            try:
                hit = Harness(
                    db,
                    CloudLLM(db, api_key=load_model_key(self._data_dir)),
                ).search(phrase, client_id=client_id)
            except Exception as exc:
                db.rollback()
                publish_notice(self._data_dir, str(exc))
            else:
                clear_notice(self._data_dir)
                candidates = getattr(hit, "candidates", None)
                if candidates is not None:
                    self.searchChoiceReady.emit(generation, candidates)
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
        drafts = []
        rows = self._db.execute(
            """
            SELECT rascunho.id,
                   CASE WHEN rascunho.estado = 'em curso'
                             AND rascunho.texto_aprovado IS NOT NULL
                        THEN rascunho.texto_aprovado ELSE rascunho.texto END AS text,
                   rascunho.estado,
                   CASE WHEN rascunho.estado = 'em curso'
                             AND rascunho.destino_aprovado IS NOT NULL
                        THEN rascunho.destino_aprovado ELSE contato.valor END AS address,
                   CASE WHEN rascunho.estado = 'em curso'
                             AND rascunho.canal_aprovado IS NOT NULL
                        THEN rascunho.canal_aprovado ELSE contato.tipo END AS channel,
                   COALESCE(rascunho.cliente_entrada_aprovado, entrada.cliente_id)
                       AS entry_client_id,
                   COALESCE(rascunho.cliente_contato_aprovado, contato.cliente_id)
                       AS contact_client_id,
                   COALESCE(entry_snapshot.nome, entry_live.nome, 'sem cliente')
                       AS entry_client,
                   COALESCE(contact_snapshot.nome, contact_live.nome, 'sem cliente')
                       AS contact_client
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            LEFT JOIN cliente AS entry_live ON entry_live.id = entrada.cliente_id
            LEFT JOIN cliente AS contact_live ON contact_live.id = contato.cliente_id
            LEFT JOIN cliente AS entry_snapshot
                ON entry_snapshot.id = rascunho.cliente_entrada_aprovado
            LEFT JOIN cliente AS contact_snapshot
                ON contact_snapshot.id = rascunho.cliente_contato_aprovado
            WHERE rascunho.estado IN ('aguardando', 'em curso')
            ORDER BY rascunho.id
            """
        )
        for row in rows:
            draft = dict(row)
            drafts.append(
                {
                    "draftId": row["id"],
                    "text": row["text"],
                    "address": row["address"],
                    "channel": row["channel"],
                    "entryClient": row["entry_client"],
                    "contactClient": row["contact_client"],
                    "clientsMatch": (
                        row["entry_client_id"] is not None
                        and row["entry_client_id"] == row["contact_client_id"]
                    ),
                    "approvalVersion": _approval_fingerprint(draft),
                    "state": row["estado"],
                }
            )
        self._drafts.replace(drafts)
        self._pendings.replace(
            [
                {
                    "entryId": row["id"],
                    "text": row["texto"],
                    "reason": row["motivo"] or "",
                    "unowned": row["cliente_id"] is None,
                }
                for row in self._db.execute(
                    """
                    SELECT entrada.id, entrada.texto, pendencia.motivo, contato.cliente_id
                    FROM entrada
                    JOIN contato ON contato.id = entrada.contato_id
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
