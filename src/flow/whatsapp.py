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
from flow.send import SendTool, hold_draft, same_client


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
        return keep_each(self._db, result.get("messages") or [], self._save)

    def _save(self, message: dict) -> int | None:
        original = str(message.get("text") or "").strip()
        note = str(message.get("media_note") or "").strip()
        if note:
            text = f"{original}\n{note}".strip() if original else note
        else:
            text = original
        attachments = list(message.get("attachments") or [])
        entry_id = open_entry(
            self._db,
            kind="whatsapp",
            value=str(message.get("from") or ""),
            message_id=scoped_message_id(
                "whatsapp",
                str(message.get("from") or ""),
                str(message.get("id") or ""),
            ),
            received_at=str(message.get("received_at") or ""),
            text=text,
            has_text=bool(original),
            has_file=bool(attachments),
            account="whatsapp",
        )
        if entry_id is None:
            return None
        for index, item in enumerate(attachments, start=1):
            name = Path(str(item.get("name") or "anexo")).name
            payload = base64.b64decode(str(item.get("data_b64") or ""), validate=True)
            store_document(self._db, self._docs, name, entry_id, index, payload)
        return entry_id


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
        """Entrega e diz se saiu. Não grava envio nem marca o rascunho."""
        if _already_sent(self._db, draft_id):
            return False
        row = self._db.execute(
            """
            SELECT rascunho.texto, rascunho.estado, contato.tipo, contato.valor,
                   entrada.cliente_id AS entrada_cliente,
                   contato.cliente_id AS contato_cliente
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None or row["estado"] != "aprovado" or row["tipo"] != "whatsapp":
            return False
        if not same_client(row["entrada_cliente"], row["contato_cliente"]):
            hold_draft(self._db, draft_id)
            return False
        session = _require_session(self._data_dir)
        self._tool = SendTool()
        self._runner(session, "send", {"to": row["valor"], "text": row["texto"]})
        return True


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

