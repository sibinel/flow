"""Remetente SMTP. A tool de envio só existe aqui, e só com rascunho aprovado."""

from __future__ import annotations

import sqlite3
import smtplib
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

    def send(self, draft_id: int, host: str, port: int, sender: str, password: str) -> bool:
        """Entrega e diz se saiu. Não grava envio nem marca o rascunho."""
        if _already_sent(self._db, draft_id):
            return False
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
        _deliver(host, port, sender, row["valor"], row["texto"], password)
        return True


def _deliver(
    host: str, port: int, sender: str, recipient: str, body: str, password: str
) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    with _smtp(host, port) as smtp:
        if host != "127.0.0.1":
            smtp.login(sender, password)
        smtp.send_message(message)


def _already_sent(connection: sqlite3.Connection, draft_id: int) -> bool:
    found = connection.execute(
        "SELECT 1 FROM envio WHERE rascunho_id = ?",
        (draft_id,),
    ).fetchone()
    return found is not None


def _smtp(host: str, port: int):
    """Em loopback o teste fala SMTP em claro. Fora dele a conexão já nasce cifrada."""
    if host == "127.0.0.1":
        return smtplib.SMTP(host, port, timeout=5)
    return smtplib.SMTP_SSL(host, port, timeout=5)
