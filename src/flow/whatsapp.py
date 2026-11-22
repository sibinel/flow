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
from datetime import datetime
from pathlib import Path

from flow.paths import package_dir, whatsapp_session
from flow.send import SendTool


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


def pair(data_dir: Path, runner=None) -> None:
    """Mostra o QR uma vez e grava a sessão fora da pasta de documentos."""
    session = whatsapp_session(data_dir)
    session.mkdir(parents=True, exist_ok=True)
    (runner or invoke)(session, "pair", {})


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
        saved: list[int] = []
        for message in result.get("messages") or []:
            entry_id = self._save(message)
            if entry_id is not None:
                saved.append(entry_id)
        return saved

    def _save(self, message: dict) -> int | None:
        contact_id = _contact_id(self._db, str(message.get("from") or ""))
        if contact_id is None:
            return None
        body = str(message.get("text") or "").strip()
        attachments = list(message.get("attachments") or [])
        if not body and not attachments:
            return None
        entry_id = self._db.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, processo_id, recebido_em, texto, estado
            ) VALUES (NULL, ?, NULL, ?, ?, 'nova')
            """,
            (contact_id, str(message.get("received_at") or ""), body),
        ).lastrowid
        for item in attachments:
            name = Path(str(item.get("name") or "anexo")).name
            payload = base64.b64decode(str(item.get("data_b64") or ""))
            self._docs.mkdir(parents=True, exist_ok=True)
            target = self._docs / name
            target.write_bytes(payload)
            self._db.execute(
                """
                INSERT INTO documento (entrada_id, caminho, nome)
                VALUES (?, ?, ?)
                """,
                (entry_id, str(target), name),
            )
        return int(entry_id)


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
        row = self._db.execute(
            """
            SELECT rascunho.texto, rascunho.estado, contato.tipo, contato.valor
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None or row["estado"] != "aprovado" or row["tipo"] != "whatsapp":
            return False
        session = _require_session(self._data_dir)
        self._tool = SendTool()
        self._runner(session, "send", {"to": row["valor"], "text": row["texto"]})
        self._db.execute(
            """
            INSERT INTO envio (rascunho_id, enviado_em, canal)
            VALUES (?, ?, 'whatsapp')
            """,
            (draft_id, datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        self._db.execute(
            "UPDATE rascunho SET estado = 'enviado' WHERE id = ?",
            (draft_id,),
        )
        return True


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


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _contact_id(connection: sqlite3.Connection, number: str) -> int | None:
    target = _digits(number)
    if not target:
        return None
    for row in connection.execute("SELECT id, valor FROM contato WHERE tipo = 'whatsapp'"):
        if _digits(row["valor"]) == target:
            return int(row["id"])
    return None
