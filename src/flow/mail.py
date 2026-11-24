"""Leitor IMAP. Só a tool de leitura. Não envia."""

from __future__ import annotations

import imaplib
import sqlite3
from email import message_from_bytes
from email import policy
from email.utils import parseaddr
from pathlib import Path

from flow.paths import document_target


class ImapReader:
    name = "leitura"

    def __init__(self, connection: sqlite3.Connection, data_dir: Path) -> None:
        self._db = connection
        self._docs = data_dir / "documentos"

    def tool_names(self) -> tuple[str, ...]:
        return (self.name,)

    def ingest(self, host: str, port: int, user: str, password: str) -> list[int]:
        """Lê a caixa e grava entrada e documento. Não envia."""
        self._docs.mkdir(parents=True, exist_ok=True)
        raw_messages = _fetch(host, port, user, password)
        return [self._save(raw) for raw in raw_messages]

    def _save(self, raw: bytes) -> int:
        message = message_from_bytes(raw, policy=policy.default)
        address = parseaddr(message.get("From", ""))[1].casefold()
        contact = self._db.execute(
            "SELECT id FROM contato WHERE tipo = 'email' AND lower(valor) = ?",
            (address,),
        ).fetchone()
        if contact is None:
            raise ValueError("contato de e-mail não cadastrado")
        body, attachments = _parts(message)
        entry_id = self._db.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, processo_id, recebido_em, texto, estado
            ) VALUES (NULL, ?, NULL, ?, ?, 'nova')
            """,
            (contact["id"], message.get("Date") or "", body),
        ).lastrowid
        saved = int(entry_id)
        for index, (filename, payload) in enumerate(attachments, start=1):
            target = document_target(self._docs, filename, saved, index)
            target.write_bytes(payload)
            self._db.execute(
                """
                INSERT INTO documento (entrada_id, caminho, nome)
                VALUES (?, ?, ?)
                """,
                (saved, str(target), filename),
            )
        return saved


def _fetch(host: str, port: int, user: str, password: str) -> list[bytes]:
    with imaplib.IMAP4(host, port) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        status, found = imap.search(None, "ALL")
        if status != "OK" or not found or not found[0]:
            return []
        messages: list[bytes] = []
        for number in found[0].split():
            status, data = imap.fetch(number, "(RFC822)")
            if status != "OK":
                continue
            for item in data:
                if isinstance(item, tuple) and len(item) > 1:
                    messages.append(item[1])
        return messages


def _parts(message) -> tuple[str, list[tuple[str, bytes]]]:
    body = ""
    attachments: list[tuple[str, bytes]] = []
    if message.is_multipart():
        for part in message.walk():
            disposition = part.get_content_disposition()
            if disposition == "attachment" or part.get_filename():
                name = Path(part.get_filename() or "anexo").name
                attachments.append((name, part.get_payload(decode=True) or b""))
            elif part.get_content_type() == "text/plain" and not body:
                content = part.get_content()
                body = content if isinstance(content, str) else str(content)
    else:
        content = message.get_content()
        body = content if isinstance(content, str) else str(content)
    return body.strip(), attachments
