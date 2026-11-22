"""Remetente SMTP. A tool de envio só existe aqui, e só com rascunho aprovado."""

from __future__ import annotations

import sqlite3
import smtplib
from datetime import datetime
from email.message import EmailMessage


class SendTool:
    name = "envio"


class SmtpSender:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._tool: SendTool | None = None

    def tool_names(self) -> tuple[str, ...]:
        if self._tool is None:
            return ()
        return (self._tool.name,)

    def send(self, draft_id: int, host: str, port: int, sender: str) -> bool:
        row = self._db.execute(
            """
            SELECT rascunho.texto, rascunho.estado, contato.valor
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None or row["estado"] != "aprovado":
            return False
        self._tool = SendTool()
        _deliver(host, port, sender, row["valor"], row["texto"])
        self._db.execute(
            """
            INSERT INTO envio (rascunho_id, enviado_em, canal)
            VALUES (?, ?, 'email')
            """,
            (draft_id, datetime.now().astimezone().isoformat(timespec="seconds")),
        )
        self._db.execute(
            "UPDATE rascunho SET estado = 'enviado' WHERE id = ?",
            (draft_id,),
        )
        return True


def _deliver(host: str, port: int, sender: str, recipient: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=5) as smtp:
        smtp.send_message(message)
