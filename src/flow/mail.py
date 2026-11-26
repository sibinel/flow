"""Leitor IMAP. Só a tool de leitura. Não envia."""

from __future__ import annotations

import hashlib
import imaplib
import json
import sqlite3
from dataclasses import dataclass
from email import message_from_bytes
from email import policy
from email.utils import parseaddr
from pathlib import Path

from flow.incoming import keep_each, open_entry
from flow.paths import store_document


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
        self._account = user
        return keep_each(self._db, _fetch(host, port, user, password), self._save)

    def _save(self, raw: bytes) -> int | None:
        message = message_from_bytes(raw, policy=policy.default)
        address = parseaddr(message.get("From", ""))[1]
        body, attachments = _parts(message)
        message_id = (message.get("Message-ID") or "").strip()
        if not message_id:
            message_id = hashlib.sha256(raw).hexdigest()
        entry_id = open_entry(
            self._db,
            kind="email",
            value=address,
            message_id=message_id,
            received_at=message.get("Date") or "",
            text=body,
            has_text=bool(body.strip()),
            has_file=bool(attachments),
            account=self._account,
        )
        if entry_id is None:
            return None
        for index, (filename, payload) in enumerate(attachments, start=1):
            store_document(self._db, self._docs, filename, entry_id, index, payload)
        return entry_id


def _fetch(host: str, port: int, user: str, password: str) -> list[bytes]:
    with _imap(host, port) as imap:
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


def _imap(host: str, port: int):
    """Em loopback o teste fala IMAP em claro. Fora dele a conexão já nasce cifrada."""
    if host == "127.0.0.1":
        return imaplib.IMAP4(host, port)
    return imaplib.IMAP4_SSL(host, port)


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


@dataclass(frozen=True)
class MailAccount:
    host: str
    port: int
    user: str
    password: str


def load_mail_account(data_dir: Path) -> MailAccount | None:
    """Conta de e-mail no disco. Sem o arquivo, a coleta não corre."""
    path = Path(data_dir) / "email.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    host = str(data.get("host") or "").strip()
    user = str(data.get("user") or "").strip()
    password = data.get("password")
    try:
        port = int(data["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if not host or not user or not isinstance(password, str) or password == "" or port <= 0:
        return None
    return MailAccount(host, port, user, password)
