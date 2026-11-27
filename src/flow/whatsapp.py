"""WhatsApp pela sessão Web do Baileys, na conta pessoal, sem a API da Meta.

O processo do aplicativo continua sendo o Python. O Node só roda a ponte
e termina; não abre navegador. Sem a sessão gravada, leitura e envio param
até um QR novo.
"""

from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from flow.incoming import keep_each, open_entry, scoped_message_id
from flow.paths import package_dir, store_document, whatsapp_session
from flow.send import (
    SendTool,
    claim_draft,
    frozen_delivery,
    hold_draft,
    record_delivery,
    release_draft,
    same_client,
)


_cursors: dict[int, tuple] = {}


def commit_cursor(connection: sqlite3.Connection) -> None:
    """Avança o cursor só depois que o banco guardou a mensagem."""
    plan = _cursors.pop(id(connection), None)
    if plan is None:
        return
    session, messages, confirmed = plan
    _advance_cursor(session, messages, confirmed)


def discard_cursor(connection: sqlite3.Connection) -> None:
    _cursors.pop(id(connection), None)


def _load_cursor(session: Path) -> dict:
    path = session / "cursor.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _advance_cursor(session: Path, messages: list, confirmed: list) -> None:
    done = {id(item) for item in confirmed}
    cursor = _load_cursor(session)
    by_chat: dict[str, list] = {}
    for message in messages:
        chat = str(message.get("from") or "")
        by_chat.setdefault(chat, []).append(message)
    for chat, batch in by_chat.items():
        batch.sort(key=lambda item: str(item.get("received_at") or ""))
        mark = cursor.get(chat)
        advanced = None
        if isinstance(mark, dict) and mark.get("id"):
            advanced = {"id": mark.get("id"), "time": mark.get("time")}
        blocked = False
        for message in batch:
            if blocked:
                continue
            if str(message.get("media_note") or "").strip():
                blocked = True
                continue
            if id(message) not in done:
                blocked = True
                continue
            advanced = {"id": message.get("id"), "time": message.get("received_at")}
        if advanced:
            cursor[chat] = advanced
    (session / "cursor.json").write_text(json.dumps(cursor), encoding="utf-8")


class SessionRequired(RuntimeError):
    """A sessão ainda não está no disco. Leitura e envio param até um QR novo."""


def session_ready(session: Path) -> bool:
    """Verdadeiro só com credenciais já registradas. Pasta vazia não conta."""
    creds = session / "creds.json"
    if not creds.is_file() or creds.stat().st_size == 0:
        return False
    try:
        data = json.loads(creds.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("registered") is True


def pair(data_dir: Path, runner=None) -> dict:
    """Mostra o QR uma vez e grava a sessão fora da pasta de documentos."""
    session = whatsapp_session(data_dir)
    session.mkdir(parents=True, exist_ok=True)
    result = (runner or invoke)(session, "pair", {})
    return result if isinstance(result, dict) else {}


class WhatsAppReader:
    name = "leitura"

    def __init__(self, connection: sqlite3.Connection, data_dir: Path, runner=None) -> None:
        self._db = connection
        self._data_dir = data_dir
        self._docs = data_dir / "documentos"
        self._runner = runner or invoke

    def tool_names(self) -> tuple[str, ...]:
        return (self.name,)

    def ingest(self) -> list[int]:
        """Lê as mensagens e grava entrada e documento. Não envia."""
        session = _require_session(self._data_dir)
        result = self._runner(session, "read", {})
        _refuse_qr(result)
        messages = list(result.get("messages") or [])
        confirmed: list[dict] = []

        def save(message: dict) -> int | None:
            entry_id = self._save(message)
            if self._durable:
                confirmed.append(message)
            return entry_id

        saved = keep_each(self._db, messages, save)
        _cursors[id(self._db)] = (session, messages, confirmed)
        return saved

    def _save(self, message: dict) -> int | None:
        self._durable = False
        original = str(message.get("text") or "").strip()
        note = str(message.get("media_note") or "").strip()
        if note:
            text = f"{original}\n{note}".strip() if original else note
        else:
            text = original
        attachments = list(message.get("attachments") or [])
        chat = str(message.get("from") or "")
        stored_id = scoped_message_id("whatsapp", chat, str(message.get("id") or ""))
        pending = self._pending_media(stored_id, chat)
        if pending is not None:
            if not attachments:
                return None
            self._store_files(pending, attachments)
            self._durable = True
            self._db.execute(
                """
                DELETE FROM pendencia
                WHERE entrada_id = ? AND motivo = 'mídia não baixada'
                """,
                (pending,),
            )
            still = self._db.execute(
                "SELECT 1 FROM pendencia WHERE entrada_id = ?",
                (pending,),
            ).fetchone()
            self._db.execute(
                """
                UPDATE entrada
                SET texto = ?, estado = ?
                WHERE id = ?
                """,
                (original or text, "pendente" if still else "nova", pending),
            )
            return pending
        entry_id = open_entry(
            self._db,
            kind="whatsapp",
            value=chat,
            message_id=stored_id,
            received_at=str(message.get("received_at") or ""),
            text=text,
            has_text=bool(original),
            has_file=bool(attachments),
            account="whatsapp",
            media_failed=bool(note) and not attachments,
        )
        if entry_id is None:
            self._durable = True
            return None
        self._store_files(entry_id, attachments)
        self._durable = True
        return entry_id

    def _pending_media(self, stored_id: str, chat: str) -> int | None:
        """Mídia que falhou não fecha o id. A próxima leitura pode completar."""
        if not stored_id:
            return None
        row = self._db.execute(
            """
            SELECT entrada.id
            FROM entrada
            JOIN pendencia ON pendencia.entrada_id = entrada.id
            WHERE entrada.canal = 'whatsapp'
              AND entrada.mensagem_id = ?
              AND entrada.conta IN ('whatsapp', ?)
              AND pendencia.motivo = 'mídia não baixada'
            """,
            (stored_id, chat),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def _store_files(self, entry_id: int, attachments: list) -> None:
        for index, item in enumerate(attachments, start=1):
            name = Path(str(item.get("name") or "anexo")).name
            payload = base64.b64decode(str(item.get("data_b64") or ""), validate=True)
            store_document(self._db, self._docs, name, entry_id, index, payload)


class WhatsAppSender:
    def __init__(self, connection: sqlite3.Connection, data_dir: Path, runner=None) -> None:
        self._db = connection
        self._data_dir = data_dir
        self._runner = runner or invoke
        self._tool: SendTool | None = None

    def tool_names(self) -> tuple[str, ...]:
        if self._tool is None:
            return ()
        return (self._tool.name,)

    def send(self, draft_id: int) -> bool:
        """Entrega o rascunho aprovado. Grava em curso antes de sair."""
        if _already_sent(self._db, draft_id):
            return False
        row = self._db.execute(
            """
            SELECT rascunho.texto, rascunho.estado, contato.tipo, contato.valor,
                   rascunho.texto_aprovado, rascunho.destino_aprovado,
                   rascunho.canal_aprovado, rascunho.cliente_entrada_aprovado,
                   rascunho.cliente_contato_aprovado,
                   entrada.cliente_id AS entrada_cliente,
                   contato.cliente_id AS contato_cliente
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None or row["estado"] != "aprovado":
            return False
        text, recipient, channel, entry_client, contact_client = frozen_delivery(row)
        if channel != "whatsapp":
            return False
        if not same_client(entry_client, contact_client):
            hold_draft(self._db, draft_id)
            return False
        session = _require_session(self._data_dir)
        if not claim_draft(self._db, draft_id):
            return False
        self._tool = SendTool()
        outcome = self._runner(session, "send", {"to": recipient, "text": text})
        if _not_handed(outcome):
            self._tool = None
            release_draft(self._db, draft_id)
            return False
        if not _was_sent(outcome):
            self._tool = None
            return False
        record_delivery(self._db, draft_id, "whatsapp")
        return True


def _not_handed(outcome) -> bool:
    """A ponte nem chegou a pedir o envio."""
    if outcome is False:
        return True
    if not isinstance(outcome, dict):
        return False
    return outcome.get("sent") is not True and outcome.get("handed") is not True


def _was_sent(outcome) -> bool:
    if outcome is True:
        return True
    return isinstance(outcome, dict) and outcome.get("sent") is True


def _already_sent(connection: sqlite3.Connection, draft_id: int) -> bool:
    found = connection.execute(
        "SELECT 1 FROM envio WHERE rascunho_id = ?",
        (draft_id,),
    ).fetchone()
    return found is not None


def invoke(session: Path, op: str, payload: dict | None = None) -> dict:
    """Roda a ponte Node e termina. Não abre sessão nova na leitura nem no envio."""
    if op != "pair" and not session_ready(session):
        raise SessionRequired(
            "sessão do WhatsApp ausente; leitura e envio param até um QR novo"
        )
    _guard(session)
    script = _bridge_script()
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("o WhatsApp pela sessão Web precisa do Node")
    body = {"op": op, "session": str(session), **(payload or {})}
    completed = subprocess.run(
        [node, str(script)],
        input=json.dumps(body).encode(),
        stdout=subprocess.PIPE,
        stderr=None if op == "pair" else subprocess.PIPE,
        cwd=str(script.parent),
        check=False,
        timeout=200 if op == "pair" else 70,
    )
    if completed.returncode == 2:
        raise SessionRequired(
            "sessão do WhatsApp ausente; leitura e envio param até um QR novo"
        )
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(detail or "ponte do WhatsApp falhou")
    text = (completed.stdout or b"").decode("utf-8", "replace").strip()
    data = json.loads(text) if text else {}
    _refuse_qr(data)
    return data


def _require_session(data_dir: Path) -> Path:
    session = whatsapp_session(data_dir)
    if not session_ready(session):
        raise SessionRequired(
            "sessão do WhatsApp ausente; leitura e envio param até um QR novo"
        )
    return session


def _refuse_qr(result: dict) -> None:
    if result.get("error") == "qr":
        raise SessionRequired(
            "sessão do WhatsApp ausente; leitura e envio param até um QR novo"
        )


def _guard(session: Path) -> None:
    resolved = session.resolve()
    pkg = package_dir().resolve()
    if "documentos" in resolved.parts:
        raise ValueError("a sessão do WhatsApp não pode ficar na pasta de documentos")
    if resolved == pkg or pkg in resolved.parents:
        raise ValueError("a sessão do WhatsApp não pode ficar dentro do pacote")


def _bridge_script() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS"))
        return base / "flow" / "baileys" / "bridge.mjs"
    return Path(__file__).resolve().parent / "baileys" / "bridge.mjs"

