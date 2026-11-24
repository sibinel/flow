"""Etapa 9: a frase acha o cliente e a resposta não traz outro."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flow.controller import Controller
from flow.harness import Harness
from flow.llm import CloudLLM
from flow.paths import package_dir
from flow.store import open_database

PHRASE = "Quando falei com o João sobre rescisão"


def _client(connection, name: str) -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')",
        (name,),
    ).lastrowid


def _contact(connection, client_id: int) -> int:
    return connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
        (client_id, f"+55119{client_id:08d}"),
    ).lastrowid


def _entry(connection, client_id: int, contact_id: int, text: str) -> int:
    return connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (?, ?, NULL, '2026-11-18T10:00:00', ?, 'arquivada')
        """,
        (client_id, contact_id, text),
    ).lastrowid


def _trecho(connection, client_id: int, entry_id: int, text: str) -> None:
    connection.execute(
        """
        INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
        VALUES (?, ?, 'whatsapp', '2026-11-18T10:00:00', ?)
        """,
        (client_id, entry_id, text),
    )


class SearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_phrase_finds_the_client_and_skips_the_other(self) -> None:
        joao = _client(self.db, "João")
        maria = _client(self.db, "Maria Souza")
        joao_phone = _contact(self.db, joao)
        maria_phone = _contact(self.db, maria)
        about = "Falei com o João sobre a rescisão do contrato de trabalho."
        other_topic = "Combinamos o inventário do imóvel INVENTARIO-JOAO."
        foreign = "A Maria comentou a rescisão no processo MARIA-UNICA."
        joao_entry = _entry(self.db, joao, joao_phone, about)
        joao_other = _entry(self.db, joao, joao_phone, other_topic)
        maria_entry = _entry(self.db, maria, maria_phone, foreign)
        _trecho(self.db, joao, joao_entry, about)
        _trecho(self.db, joao, joao_other, other_topic)
        _trecho(self.db, maria, maria_entry, foreign)
        self.db.commit()

        seen: list[dict] = []

        def transport(url: str, body: dict, headers: dict) -> dict:
            seen.append(body)
            piece = json.loads(body["messages"][1]["content"])
            return {"choices": [{"message": {"content": "\n".join(piece["trechos"])}}]}

        harness = Harness(self.db, CloudLLM(self.db, transport=transport))
        self.assertEqual(harness.tool_names(), ("leitura",))
        self.assertNotIn("envio", harness.tool_names())
        hit = harness.search(PHRASE)

        piece = json.loads(seen[0]["messages"][1]["content"])
        draft = self.db.execute(
            "SELECT entrada_id, texto, estado FROM rascunho WHERE id = ?",
            (hit.draft_id,),
        ).fetchone()
        self.assertEqual(hit.client_id, joao)
        self.assertEqual(harness.read.loaded, [joao])
        self.assertEqual(hit.snippets, (about,))
        self.assertEqual(piece["tarefa"], "busca")
        self.assertEqual(piece["mensagem"], PHRASE)
        self.assertEqual(piece["trechos"], [about])
        self.assertEqual(draft["entrada_id"], joao_entry)
        self.assertEqual(draft["estado"], "aguardando")
        self.assertEqual(draft["texto"], about)
        self.assertNotIn("MARIA-UNICA", draft["texto"])
        self.assertNotIn("INVENTARIO-JOAO", draft["texto"])
        self.assertNotIn("MARIA-UNICA", json.dumps(seen, ensure_ascii=False))
        self.assertNotIn("Maria Souza", json.dumps(seen, ensure_ascii=False))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)


class WindowDoesNotSearchTest(unittest.TestCase):
    def test_window_does_not_search(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("harness", "flow.harness"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
