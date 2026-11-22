"""Classificador fixo. Um cliente por vez, só com a tool de leitura."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_INTRO = re.compile(
    r"(?i)\b(?:sou|aqui é|meu nome é)\s+(?:a|o|uma|um)?\s*([a-zà-ÿ]{2,})"
)
_KNOWN = frozenset({"cliente", "prospecção"})


@dataclass(frozen=True)
class ProcessView:
    id: int
    number: str
    title: str


@dataclass(frozen=True)
class ClientView:
    id: int
    name: str
    status: str
    processes: tuple[ProcessView, ...]


class ReadTool:
    """Lê só o cliente pedido: nome, contatos e processos dele."""

    name = "leitura"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self.loaded: list[int] = []

    def load_client(self, client_id: int) -> ClientView | None:
        self.loaded.append(client_id)
        row = self._db.execute(
            "SELECT id, nome, status FROM cliente WHERE id = ?",
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        processes = tuple(
            ProcessView(item["id"], item["número"], item["título"])
            for item in self._db.execute(
                """
                SELECT id, "número", "título"
                FROM processo WHERE cliente_id = ? ORDER BY id
                """,
                (client_id,),
            )
        )
        return ClientView(row["id"], row["nome"], row["status"], processes)


class Classifier:
    def __init__(self, read: ReadTool) -> None:
        self.read = read

    def tool_names(self) -> tuple[str, ...]:
        return (self.read.name,)

    def classify(self, entry_id: int) -> str:
        entry = self.read._db.execute(
            "SELECT id, contato_id, texto FROM entrada WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if entry is None:
            raise ValueError("entrada não existe")
        contact = self.read._db.execute(
            "SELECT id, cliente_id FROM contato WHERE id = ?",
            (entry["contato_id"],),
        ).fetchone()
        if contact is None:
            raise ValueError("contato não existe")
        client = self.read.load_client(contact["cliente_id"])
        if client is None:
            raise ValueError("cliente não existe")

        reason = _reason(entry["texto"], client)
        if reason is None:
            cited = _cited(entry["texto"], client.processes)
            self.read._db.execute(
                """
                UPDATE entrada
                SET cliente_id = ?, processo_id = ?, estado = 'arquivada'
                WHERE id = ?
                """,
                (client.id, cited[0], entry_id),
            )
            self.read._db.execute("DELETE FROM pendencia WHERE entrada_id = ?", (entry_id,))
            return "arquivada"

        self.read._db.execute(
            """
            UPDATE entrada
            SET cliente_id = NULL, processo_id = NULL, estado = 'pendente'
            WHERE id = ?
            """,
            (entry_id,),
        )
        self.read._db.execute("DELETE FROM pendencia WHERE entrada_id = ?", (entry_id,))
        self.read._db.execute(
            "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, ?)",
            (entry_id, reason),
        )
        return reason


def _reason(text: str, client: ClientView) -> str | None:
    introduced = _introduced_name(text)
    if introduced is not None and not _same_person(introduced, client.name):
        return "nome diferente"
    if client.status not in _KNOWN:
        return "contato novo"
    cited = _cited(text, client.processes)
    if len(cited) != 1:
        return "processo não citado"
    return None


def _introduced_name(text: str) -> str | None:
    found = _INTRO.search(text)
    if found is None:
        return None
    return found.group(1)


def _same_person(introduced: str, owner: str) -> bool:
    token = introduced.casefold()
    parts = {part.casefold() for part in owner.split() if part}
    return token in parts or token == owner.casefold()


def _cited(text: str, processes: tuple[ProcessView, ...]) -> list[int]:
    folded = text.casefold()
    hits: list[int] = []
    for process in processes:
        number = process.number.casefold()
        title = process.title.casefold()
        if number and number in folded:
            hits.append(process.id)
        elif len(title) >= 4 and title in folded:
            hits.append(process.id)
    return hits
