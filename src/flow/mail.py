"""Leitor IMAP. Só a tool de leitura. Não envia."""

from __future__ import annotations

import hashlib
import imaplib
import json
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import date, timedelta
from email import message_from_bytes
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path

from flow.incoming import IngestResult, keep_each, open_entry
from flow.paths import store_document


class ImapReader:
    name = "leitura"

    def __init__(self, connection: sqlite3.Connection, data_dir: Path) -> None:
        self._db = connection
        self._docs = data_dir / "documentos"

    def tool_names(self) -> tuple[str, ...]:
        return (self.name,)

    def ingest(self, host: str, port: int, user: str, password: str) -> IngestResult:
        """Lê só o que chegou depois do cursor e grava entrada e documento. Não envia.

        O cursor vai para `store_meta` na mesma transação das entradas: ou os dois
        ficam, ou nenhum fica.
        """
        self._docs.mkdir(parents=True, exist_ok=True)
        self._account = user
        key = _cursor_key(host, user)
        batch = _fetch(host, port, user, password, _load_cursor(self._db, key))
        items = [item if isinstance(item, FetchedMail) else FetchedMail(None, item) for item in batch]
        result = keep_each(self._db, [item.raw for item in items], self._save)
        validity = getattr(batch, "uidvalidity", None)
        if validity is not None:
            failed = {failure.index for failure in result.failures}
            last = getattr(batch, "start_uid", 0)
            for index, item in enumerate(items):
                if index in failed or item.uid is None:
                    break
                last = max(last, item.uid)
            if last > 0:
                _save_cursor(self._db, key, validity, last)
        return result

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


FIRST_READ_DAYS = 7
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class FetchedMail:
    uid: int | None
    raw: bytes


class MailBatch(list):
    """Mensagens em ordem de UID, com a validade da caixa e o ponto de partida."""

    def __init__(self, items=(), uidvalidity: int | None = None, start_uid: int = 0) -> None:
        super().__init__(items)
        self.uidvalidity = uidvalidity
        self.start_uid = start_uid


def _cursor_key(host: str, user: str) -> str:
    return f"imap_cursor:{host.strip().casefold()}:{user.strip().casefold()}"


def _load_cursor(connection: sqlite3.Connection, key: str) -> dict | None:
    row = connection.execute("SELECT value FROM store_meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["value"])
        return {"uidvalidity": int(data["uidvalidity"]), "uid": int(data["uid"])}
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _save_cursor(connection: sqlite3.Connection, key: str, validity: int, uid: int) -> None:
    connection.execute(
        """
        INSERT INTO store_meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, json.dumps({"uidvalidity": int(validity), "uid": int(uid)})),
    )


def _since(today: date | None = None) -> str:
    """Data do IMAP SINCE, com mês em inglês como o protocolo pede."""
    day = (today or date.today()) - timedelta(days=FIRST_READ_DAYS)
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _response_number(imap, name: str) -> int | None:
    try:
        _kind, values = imap.response(name)
    except Exception:
        return None
    for value in values or []:
        if value is None:
            continue
        try:
            return int(value.decode() if isinstance(value, bytes) else value)
        except (TypeError, ValueError):
            continue
    return None


def _fetch(
    host: str, port: int, user: str, password: str, cursor: dict | None = None
) -> MailBatch:
    """Só o que veio depois do cursor. Sem cursor, ou caixa recriada, só os últimos dias."""
    with _imap(host, port) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        validity = _response_number(imap, "UIDVALIDITY")
        if validity is None:
            raise RuntimeError("o servidor IMAP não informou UIDVALIDITY")
        if cursor is not None and cursor["uidvalidity"] == validity:
            start = cursor["uid"]
            status, found = imap.uid("SEARCH", "UID", f"{start + 1}:*")
        else:
            start = 0
            status, found = imap.uid("SEARCH", "SINCE", _since())
        if status != "OK":
            raise RuntimeError("a busca IMAP falhou")
        uids = sorted(
            {int(item) for item in (found[0] or b"").split() if item.isdigit()} if found else set()
        )
        # "N:*" sempre devolve a última mensagem, mesmo com UID menor que N.
        uids = [uid for uid in uids if uid > start]
        batch = MailBatch(uidvalidity=validity, start_uid=start)
        for uid in uids:
            status, data = imap.uid("FETCH", str(uid), "(RFC822)")
            raw = next(
                (item[1] for item in data or [] if isinstance(item, tuple) and len(item) > 1),
                None,
            )
            if status != "OK" or raw is None:
                # O cursor não pode passar por cima de uma mensagem que não veio.
                break
            batch.append(FetchedMail(uid, raw))
        return batch


def verified_context() -> ssl.SSLContext:
    """Fora do loopback o certificado e o nome do servidor são conferidos."""
    context = ssl.create_default_context()
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


def _imap(host: str, port: int):
    """Em loopback o teste fala IMAP em claro. Fora dele a conexão já nasce cifrada."""
    if host == "127.0.0.1":
        return imaplib.IMAP4(host, port)
    return imaplib.IMAP4_SSL(host, port, ssl_context=verified_context())


class _HtmlText(HTMLParser):
    """Texto visível do HTML. Script e style ficam fora."""

    _BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in {"script", "style", "head", "title"}:
            self._hidden += 1
        elif tag in self._BREAKS:
            self._parts.append("\n")

    def handle_endtag(self, tag) -> None:
        if tag in {"script", "style", "head", "title"} and self._hidden:
            self._hidden -= 1
        elif tag in self._BREAKS:
            self._parts.append("\n")

    def handle_data(self, data) -> None:
        if not self._hidden:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_text(html: str) -> str:
    parser = _HtmlText()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def _text_of(part) -> str:
    content = part.get_content()
    return content if isinstance(content, str) else ""


def _parts(message) -> tuple[str, list[tuple[str, bytes]]]:
    body = ""
    html = ""
    attachments: list[tuple[str, bytes]] = []
    if not message.is_multipart():
        return _single(message)
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        if disposition == "attachment" or part.get_filename():
            name = Path(part.get_filename() or "anexo").name
            attachments.append((name, part.get_payload(decode=True) or b""))
        elif part.get_content_type() == "text/plain" and not body:
            body = _text_of(part)
        elif part.get_content_type() == "text/html" and not html:
            html = _text_of(part)
    if not body.strip() and html:
        body = html_text(html)
    return body.strip(), attachments


def _single(message) -> tuple[str, list[tuple[str, bytes]]]:
    """Uma parte só, com nome ou disposição de anexo, vira documento."""
    filename = message.get_filename()
    disposition = message.get_content_disposition()
    if disposition == "attachment" or filename:
        name = Path(filename or "anexo").name
        payload = message.get_payload(decode=True) or b""
        return "", [(name, payload)]
    content = message.get_content()
    body = content if isinstance(content, str) else ""
    if message.get_content_type() == "text/html":
        body = html_text(body)
    return body.strip(), []


@dataclass(frozen=True)
class MailAccount:
    imap_host: str
    imap_port: int
    user: str
    password: str


def load_mail_account(data_dir: Path) -> MailAccount | None:
    """Conta de coleta no disco. Sem o arquivo, a coleta não corre.

    O arquivo antigo, com um host e uma porta, chega até aqui.
    Os campos de envio ficam no remetente.
    """
    data = _read_mail_file(data_dir)
    if data is None:
        return None
    user = str(data.get("user") or "").strip()
    password = data.get("password")
    imap_host = _field(data, "imap_host", "host")
    imap_port = _number(data, "imap_port", "port")
    if (
        not imap_host
        or imap_port is None
        or not user
        or not isinstance(password, str)
        or password == ""
    ):
        return None
    return MailAccount(imap_host, imap_port, user, password)


def _read_mail_file(data_dir: Path) -> dict | None:
    path = Path(data_dir) / "email.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _field(data: dict, *keys: str) -> str:
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str):
            return value.strip()
        return ""
    return ""


def _number(data: dict, *keys: str) -> int | None:
    for key in keys:
        if key not in data:
            continue
        try:
            number = int(data[key])
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None
    return None
