"""Harness da busca. Uma frase, um cliente, só os trechos que o FTS5 devolve."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from flow.classify import ReadTool
from flow.llm import CloudLLM
from flow.store import search_trechos_do_cliente

_MARKER = " sobre "


@dataclass(frozen=True)
class SearchHit:
    client_id: int
    draft_id: int
    snippets: tuple[str, ...]


class Harness:
    def __init__(self, connection: sqlite3.Connection, llm: CloudLLM) -> None:
        self._db = connection
        self._llm = llm
        self.read = ReadTool(connection)

    def tool_names(self) -> tuple[str, ...]:
        return (self.read.name,)

    def search(self, phrase: str) -> SearchHit:
        """Acha o cliente na frase, lê os trechos dele e grava o rascunho."""
        client_id = _named_client(self._db, phrase)
        if self.read.load_client(client_id) is None:
            raise ValueError("cliente não existe")
        topic = _topic(phrase)
        rows = search_trechos_do_cliente(self._db, client_id, topic)
        if not rows:
            raise ValueError("nenhum trecho desse cliente")
        snippets = tuple(row["texto"] for row in rows)
        answer = self._llm.complete(
            "busca",
            client_id=client_id,
            message=phrase,
            query=topic,
        )
        for foreign in self._db.execute(
            "SELECT texto FROM trecho WHERE cliente_id != ?",
            (client_id,),
        ):
            if foreign["texto"] and foreign["texto"] in answer:
                raise RuntimeError("resposta com trecho de outro cliente")
        draft_id = self._db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, ?, 'aguardando')
            """,
            (rows[0]["entrada_id"], answer),
        ).lastrowid
        self._db.commit()
        return SearchHit(client_id, int(draft_id), snippets)


def _named_client(connection: sqlite3.Connection, phrase: str) -> int:
    folded = phrase.casefold()
    best_score = -1
    found: list[int] = []
    for row in connection.execute("SELECT id, nome FROM cliente ORDER BY id"):
        name = str(row["nome"]).casefold().strip()
        parts = [name, *name.split()] if name else []
        score = None
        for part in parts:
            if len(part) < 3:
                continue
            if re.search(rf"(?<!\w){re.escape(part)}(?!\w)", folded) is None:
                continue
            score = len(part)
            break
        if score is None:
            continue
        if score > best_score:
            best_score = score
            found = [int(row["id"])]
        elif score == best_score:
            found.append(int(row["id"]))
    if len(found) != 1:
        if found:
            raise ValueError("cliente ambíguo")
        raise ValueError("cliente não encontrado na frase")
    return found[0]


def _topic(phrase: str) -> str:
    folded = phrase.casefold()
    index = folded.rfind(_MARKER)
    if index < 0:
        raise ValueError("tema não encontrado na frase")
    topic = phrase[index + len(_MARKER) :].strip()
    if not topic:
        raise ValueError("tema não encontrado na frase")
    return topic
