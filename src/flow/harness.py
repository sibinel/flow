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


@dataclass(frozen=True)
class SearchCandidate:
    client_id: int
    name: str


@dataclass(frozen=True)
class SearchChoice:
    candidates: tuple[SearchCandidate, ...]


class Harness:
    def __init__(self, connection: sqlite3.Connection, llm: CloudLLM) -> None:
        self._db = connection
        self._llm = llm
        self.read = ReadTool(connection)

    def tool_names(self) -> tuple[str, ...]:
        return (self.read.name,)

    def candidates(self, phrase: str) -> tuple[SearchCandidate, ...]:
        prefix, _ = _split_phrase(phrase)
        return _named_clients(self._db, prefix)

    def search(self, phrase: str, client_id: int = 0) -> SearchHit | SearchChoice:
        """Acha o cliente na frase, lê os trechos dele e grava o rascunho."""
        candidates = self.candidates(phrase)
        if not candidates:
            raise ValueError("cliente não encontrado na frase")
        if client_id:
            if client_id not in {candidate.client_id for candidate in candidates}:
                raise ValueError("cliente selecionado não corresponde à frase")
        elif len(candidates) > 1:
            return SearchChoice(candidates)
        else:
            client_id = candidates[0].client_id
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


def _named_clients(connection: sqlite3.Connection, prefix: str) -> tuple[SearchCandidate, ...]:
    folded = prefix.casefold()
    found = []
    for row in connection.execute("SELECT id, nome FROM cliente ORDER BY id"):
        name = str(row["nome"]).casefold().strip()
        parts = [name, *name.split()] if name else []
        for part in parts:
            if len(part) < 3:
                continue
            if re.search(rf"(?<!\w){re.escape(part)}(?!\w)", folded) is None:
                continue
            found.append(SearchCandidate(int(row["id"]), str(row["nome"])))
            break
    return tuple(found)


def _split_phrase(phrase: str) -> tuple[str, str]:
    folded = phrase.casefold()
    index = folded.rfind(_MARKER)
    if index < 0:
        raise ValueError("tema não encontrado na frase")
    topic = phrase[index + len(_MARKER) :].strip()
    if not topic:
        raise ValueError("tema não encontrado na frase")
    return phrase[:index], topic


def _topic(phrase: str) -> str:
    return _split_phrase(phrase)[1]
