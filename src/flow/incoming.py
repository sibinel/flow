"""Uma mensagem do canal vira entrada. Id repetido não cria outra."""

from __future__ import annotations

import sqlite3

from flow.when import stored_received


def already_stored(connection: sqlite3.Connection, message_id: str) -> bool:
    if not message_id:
        return False
    found = connection.execute(
        "SELECT 1 FROM entrada WHERE mensagem_id = ?",
        (message_id,),
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
) -> int | None:
    """Grava a entrada. Id já visto devolve None. Contato sem cliente fica pendente."""
    if already_stored(connection, message_id):
        return None
    contact_id, known = contact_for(connection, kind, value)
    reason = None
    if not known:
        reason = "contato novo"
    elif not has_text and not has_file:
        reason = "mídia não baixada"
    received = stored_received(received_at)
    if received is None and reason is None:
        reason = "data ilegível"
    body = text.strip()
    if not body:
        body = reason or ""
    entry_id = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado, mensagem_id
        ) VALUES (NULL, ?, NULL, ?, ?, ?, ?)
        """,
        (
            contact_id,
            received if received is not None else (received_at or ""),
            body,
            "pendente" if reason else "nova",
            message_id or None,
        ),
    ).lastrowid
    saved = int(entry_id)
    if reason is not None:
        connection.execute(
            "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, ?)",
            (saved, reason),
        )
    return saved


def keep_each(connection: sqlite3.Connection, items, save) -> list[int]:
    """Uma mensagem que falha fica de fora. As outras permanecem na transação."""
    saved: list[int] = []
    for item in items:
        connection.execute("SAVEPOINT mensagem")
        try:
            entry_id = save(item)
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT mensagem")
            connection.execute("RELEASE SAVEPOINT mensagem")
            continue
        connection.execute("RELEASE SAVEPOINT mensagem")
        if entry_id is not None:
            saved.append(int(entry_id))
    return saved


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())
