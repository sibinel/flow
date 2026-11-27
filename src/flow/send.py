"""Remetente SMTP. A tool de envio só existe aqui, e só com rascunho aprovado."""

from __future__ import annotations

import json
import sqlite3
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from flow.mail import _number, _read_mail_file, verified_context


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
        """Entrega o rascunho aprovado. Grava em curso antes de sair."""
        if _already_sent(self._db, draft_id):
            return False
        row = self._db.execute(
            """
            SELECT rascunho.texto, rascunho.estado, contato.valor, contato.tipo,
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
        text, recipient, _channel, entry_client, contact_client = frozen_delivery(row)
        if not same_client(entry_client, contact_client):
            hold_draft(self._db, draft_id)
            return False
        if not claim_draft(self._db, draft_id):
            return False
        self._tool = SendTool()
        delivered = _deliver(host, port, sender, recipient, text, password)
        if delivered is False:
            self._tool = None
            release_draft(self._db, draft_id)
            return False
        record_delivery(self._db, draft_id, "email")
        return True


def claim_draft(connection: sqlite3.Connection, draft_id: int) -> bool:
    """Marca em curso e grava antes da entrega. Se o processo cair, fica assim."""
    changed = connection.execute(
        """
        UPDATE rascunho SET estado = 'em curso'
        WHERE id = ? AND estado = 'aprovado'
        """,
        (draft_id,),
    )
    if changed.rowcount != 1:
        return False
    connection.commit()
    return True


def release_draft(connection: sqlite3.Connection, draft_id: int) -> None:
    """A entrega devolveu falso. O próximo ciclo pode tentar de novo."""
    connection.execute(
        """
        UPDATE rascunho SET estado = 'aprovado'
        WHERE id = ? AND estado = 'em curso'
        """,
        (draft_id,),
    )
    connection.commit()


def record_delivery(connection: sqlite3.Connection, draft_id: int, channel: str) -> None:
    """Registra a saída e tira o rascunho da fila."""
    sent_at = datetime.now().astimezone().isoformat(timespec="seconds")
    connection.execute(
        """
        INSERT INTO envio (rascunho_id, enviado_em, canal)
        VALUES (?, ?, ?)
        """,
        (draft_id, sent_at, channel),
    )
    connection.execute(
        "UPDATE rascunho SET estado = 'enviado' WHERE id = ?",
        (draft_id,),
    )
    connection.commit()


def _deliver(
    host: str, port: int, sender: str, recipient: str, body: str, password: str
) -> bool:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    with _smtp(host, port) as smtp:
        if host != "127.0.0.1":
            smtp.login(sender, password)
        smtp.send_message(message)
    return True


def frozen_delivery(row) -> tuple[str, str, str, int | None, int | None]:
    """O envio lê a cópia feita na aprovação. Sem ela, usa a linha atual."""
    if row["texto_aprovado"] is not None:
        return (
            row["texto_aprovado"],
            row["destino_aprovado"] or "",
            row["canal_aprovado"] or "",
            row["cliente_entrada_aprovado"],
            row["cliente_contato_aprovado"],
        )
    return (
        row["texto"],
        row["valor"],
        row["tipo"],
        row["entrada_cliente"],
        row["contato_cliente"],
    )


def same_client(entry_client: int | None, contact_client: int | None) -> bool:
    """O envio só sai se o contato pertence ao cliente da entrada."""
    return entry_client is not None and entry_client == contact_client


def hold_draft(connection: sqlite3.Connection, draft_id: int) -> None:
    """Devolve o rascunho para a fila e solta a cópia. Nada foi entregue."""
    connection.execute(
        """
        UPDATE rascunho
        SET estado = 'aguardando',
            texto_aprovado = NULL,
            destino_aprovado = NULL,
            canal_aprovado = NULL,
            cliente_entrada_aprovado = NULL,
            cliente_contato_aprovado = NULL
        WHERE id = ? AND estado = 'aprovado'
        """,
        (draft_id,),
    )


def _already_sent(connection: sqlite3.Connection, draft_id: int) -> bool:
    found = connection.execute(
        "SELECT 1 FROM envio WHERE rascunho_id = ?",
        (draft_id,),
    ).fetchone()
    return found is not None


def load_smtp_target(data_dir: Path) -> tuple[str, int] | None:
    """Host e porta de envio. Sem esses campos, o correio não sai."""
    data = _read_mail_file(data_dir)
    if data is None:
        return None
    host = data.get("smtp_host")
    port = _number(data, "smtp_port")
    if not isinstance(host, str) or not host.strip() or port is None:
        return None
    return host.strip(), port


def _smtp(host: str, port: int):
    """Em loopback o teste fala SMTP em claro. Fora dele a conexão já nasce cifrada."""
    if host == "127.0.0.1":
        return smtplib.SMTP(host, port, timeout=5)
    return smtplib.SMTP_SSL(host, port, timeout=5, context=verified_context())
