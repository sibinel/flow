"""Remetente SMTP. A tool de envio só existe aqui, e só com rascunho aprovado."""

from __future__ import annotations

import json
import re
import sqlite3
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import NamedTuple

from flow.mail import _number, _read_mail_file, verified_context
from flow.store import add_sent_trecho


class SendTool:
    name = "envio"


class Delivery(NamedTuple):
    """A cópia aprovada, reservada para sair. `reply_to` é o Message-ID respondido."""

    text: str
    recipient: str
    subject: str = ""
    reply_to: str = ""


_MESSAGE_ID = re.compile(r"<[^<>@\s]+@[^<>@\s]+>")


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
        delivery = reserve_delivery(self._db, draft_id, "email", _single_email_recipient)
        if delivery is None:
            return False
        self._tool = SendTool()
        delivered = _deliver(
            host,
            port,
            sender,
            delivery.recipient,
            delivery.text,
            password,
            delivery.subject,
            delivery.reply_to,
        )
        if delivered is False:
            self._tool = None
            release_draft(self._db, draft_id)
            return False
        record_delivery(self._db, draft_id, "email")
        return True


def reserve_delivery(
    connection: sqlite3.Connection,
    draft_id: int,
    channel: str,
    valid_recipient,
) -> Delivery | None:
    """Lê a cópia e a reserva sob o mesmo bloqueio de escrita."""
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if _already_sent(connection, draft_id):
            connection.rollback()
            return None
        row = connection.execute(
            """
            SELECT rascunho.estado, rascunho.texto_aprovado,
                   rascunho.destino_aprovado, rascunho.canal_aprovado,
                   rascunho.cliente_entrada_aprovado,
                   rascunho.cliente_contato_aprovado,
                   rascunho.assunto_aprovado,
                   entrada.mensagem_id AS entrada_mensagem,
                   entrada.canal AS entrada_canal,
                   entrada.cliente_id AS entrada_cliente,
                   contato.cliente_id AS contato_cliente,
                   contato.tipo AS contato_tipo
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if row is None or row["estado"] != "aprovado":
            connection.rollback()
            return None
        try:
            text, recipient, approved_channel, entry_client, contact_client = frozen_delivery(row)
        except ValueError:
            connection.rollback()
            return None
        if approved_channel != channel:
            connection.rollback()
            return None
        if (
            row["contato_tipo"] != approved_channel
            or not same_client(entry_client, contact_client)
            or entry_client != row["entrada_cliente"]
            or contact_client != row["contato_cliente"]
            or not valid_recipient(recipient)
        ):
            _return_for_review(connection, draft_id)
            connection.commit()
            return None
        changed = connection.execute(
            "UPDATE rascunho SET estado = 'em curso' WHERE id = ? AND estado = 'aprovado'",
            (draft_id,),
        )
        if changed.rowcount != 1:
            connection.rollback()
            return None
        connection.commit()
        subject = str(row["assunto_aprovado"] or "") if channel == "email" else ""
        reply_to = ""
        if channel == "email" and row["entrada_canal"] in (None, "email"):
            found = _MESSAGE_ID.fullmatch(str(row["entrada_mensagem"] or "").strip())
            reply_to = found.group(0) if found else ""
        return Delivery(text, recipient, subject, reply_to)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


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
    add_sent_trecho(connection, draft_id, sent_at)
    connection.commit()


def _close_smtp(smtp) -> None:
    try:
        smtp.quit()
    except Exception:
        try:
            smtp.close()
        except Exception:
            return


def _deliver(
    host: str,
    port: int,
    sender: str,
    recipient: str,
    body: str,
    password: str,
    subject: str = "",
    reply_to: str = "",
) -> bool:
    """Falha ao abrir ou entrar devolve falso. Falha depois do envio sobe."""
    try:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        if subject:
            message["Subject"] = subject
        if reply_to:
            # Respondendo na mesma conversa do cliente.
            message["In-Reply-To"] = reply_to
            message["References"] = reply_to
        message.set_content(body)
    except Exception:
        return False
    try:
        smtp = _smtp(host, port)
    except Exception:
        return False
    try:
        if host != "127.0.0.1":
            smtp.login(sender, password)
    except Exception:
        _close_smtp(smtp)
        return False
    try:
        refused = smtp.send_message(message)
    except Exception:
        _close_smtp(smtp)
        raise
    _close_smtp(smtp)
    return not refused


def frozen_delivery(row) -> tuple[str, str, str, int | None, int | None]:
    """Retorna somente a cópia completa criada pela aprovação humana."""
    fields = (
        "texto_aprovado",
        "destino_aprovado",
        "canal_aprovado",
        "cliente_entrada_aprovado",
        "cliente_contato_aprovado",
    )
    if any(row[field] is None for field in fields):
        raise ValueError("rascunho sem cópia completa de aprovação")
    return tuple(row[field] for field in fields)  # type: ignore[return-value]


def same_client(entry_client: int | None, contact_client: int | None) -> bool:
    """O envio só sai se o contato pertence ao cliente da entrada."""
    return entry_client is not None and entry_client == contact_client


def _return_for_review(connection: sqlite3.Connection, draft_id: int) -> None:
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


def _single_email_recipient(recipient: str) -> bool:
    return bool(
        re.fullmatch(r"[^@\s,<>:;]+@[^@\s,<>:;]+", recipient.strip())
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


STARTTLS_PORT = 587


def _smtp(host: str, port: int):
    """Em loopback o teste fala SMTP em claro. Fora dele, nunca há login sem cifra.

    Na porta 587 a conexão sobe com STARTTLS antes do login; nas outras, já nasce
    cifrada (SSL direto, como na 465).
    """
    if host == "127.0.0.1":
        return smtplib.SMTP(host, port, timeout=5)
    if port == STARTTLS_PORT:
        smtp = smtplib.SMTP(host, port, timeout=5)
        try:
            smtp.ehlo()
            smtp.starttls(context=verified_context())
            smtp.ehlo()
        except Exception:
            _close_smtp(smtp)
            raise
        return smtp
    return smtplib.SMTP_SSL(host, port, timeout=5, context=verified_context())
