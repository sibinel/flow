"""Uma mensagem do canal vira entrada. Id repetido não cria outra."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from flow.paths import begin_files, cleanup_attempt, end_files
from flow.when import stored_received


def scoped_message_id(kind: str, chat: str, message_id: str) -> str:
    """No WhatsApp o id gravado inclui o chat. Nos outros canais, fica como chegou."""
    raw = (message_id or "").strip()
    if kind != "whatsapp" or not raw:
        return raw
    key = _digits(chat) or chat.strip()
    if not key:
        return raw
    prefix = f"{key}:"
    if raw.startswith(prefix):
        return raw
    return prefix + raw


def already_stored(
    connection: sqlite3.Connection,
    canal: str,
    conta: str,
    message_id: str,
    sender: str = "",
) -> bool:
    """O mesmo id em outra conta, ou em outro chat, não é a mesma mensagem."""
    if not message_id:
        return False
    if canal == "whatsapp":
        found = connection.execute(
            """
            SELECT 1 FROM entrada
            WHERE canal = ? AND mensagem_id = ? AND conta IN (?, ?)
            """,
            (canal, message_id, conta, sender or conta),
        ).fetchone()
        return found is not None
    found = connection.execute(
        """
        SELECT 1 FROM entrada
        WHERE canal = ? AND mensagem_id = ? AND conta = ?
        """,
        (canal, message_id, conta),
    ).fetchone()
    return found is not None


def contact_for(connection: sqlite3.Connection, kind: str, value: str) -> tuple[int, bool]:
    """Devolve o contato e se ele já pertence a um cliente. Sem cliente, cria a linha."""
    if kind == "email":
        stored = value.casefold().strip()
        row = connection.execute(
            """
            SELECT id, cliente_id FROM contato
            WHERE tipo = 'email' AND lower(valor) = ?
            """,
            (stored,),
        ).fetchone()
    else:
        digits = _digits(value)
        row = None
        for item in connection.execute(
            "SELECT id, cliente_id, valor FROM contato WHERE tipo = 'whatsapp'"
        ):
            if digits and _digits(item["valor"]) == digits:
                row = item
                break
        stored = f"+{digits}" if digits else value.strip()
    if row is not None:
        return int(row["id"]), row["cliente_id"] is not None
    created = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (NULL, ?, ?)",
        (kind, stored or "(sem endereço)"),
    ).lastrowid
    return int(created), False


def open_entry(
    connection: sqlite3.Connection,
    *,
    kind: str,
    value: str,
    message_id: str,
    received_at: str,
    text: str,
    has_text: bool,
    has_file: bool,
    account: str,
    media_failed: bool = False,
) -> int | None:
    """Grava a entrada. Id já visto nesta conta devolve None. Contato sem cliente fica pendente."""
    stored_id = scoped_message_id(kind, value, message_id)
    if already_stored(connection, kind, account, stored_id, value):
        return None
    contact_id, known = contact_for(connection, kind, value)
    reasons: list[str] = []
    if not known:
        reasons.append("contato novo")
    if media_failed or (known and not has_text and not has_file):
        reasons.append("mídia não baixada")
    received = stored_received(received_at)
    if received is None and not reasons:
        reasons.append("data ilegível")
    body = text.strip()
    if not body:
        body = reasons[0] if reasons else ""
    entry_id = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado,
            mensagem_id, canal, conta
        ) VALUES (NULL, ?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            contact_id,
            received if received is not None else (received_at or ""),
            body,
            "pendente" if reasons else "nova",
            stored_id or None,
            kind,
            account,
        ),
    ).lastrowid
    saved = int(entry_id)
    for reason in reasons:
        connection.execute(
            "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, ?)",
            (saved, reason),
        )
    return saved


@dataclass(frozen=True)
class PersistenceFailure:
    index: int
    error: str


class IngestResult(list[int]):
    def __init__(self) -> None:
        super().__init__()
        self.duplicates = 0
        self.failures: list[PersistenceFailure] = []
        self.warnings: list[str] = []

    @property
    def saved(self) -> list[int]:
        return list(self)


def keep_each(connection: sqlite3.Connection, items, save) -> IngestResult:
    """Mantém sucessos e comunica falhas sem confirmar a transação externa."""
    saved = IngestResult()
    if not connection.in_transaction:
        connection.execute("BEGIN")
    for index, item in enumerate(items):
        connection.execute("SAVEPOINT mensagem")
        files = begin_files()
        try:
            entry_id = save(item)
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT mensagem")
            connection.execute("RELEASE SAVEPOINT mensagem")
            try:
                cleanup_attempt(connection, files)
            except OSError:
                pass
            finally:
                end_files()
            saved.failures.append(PersistenceFailure(index, str(exc)))
            continue
        connection.execute("RELEASE SAVEPOINT mensagem")
        end_files()
        if entry_id is not None:
            saved.append(int(entry_id))
        else:
            saved.duplicates += 1
    return saved


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())
