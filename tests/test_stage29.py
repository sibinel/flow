"""Busca exige confirmação inequívoca antes de chamar o modelo."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller

from flow.harness import Harness
from flow.llm import CloudLLM
from flow.store import open_database


class SearchSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_database(Path(self.tmp.name))
        self.calls = []

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def client(self, name: str, text: str) -> int:
        client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')", (name,)
        ).lastrowid
        contact = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', ?)",
            (client, f"{client}@example.com"),
        ).lastrowid
        entry = self.db.execute(
            "INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado) "
            "VALUES (?, ?, '2026-11-18', 'oi', 'arquivada')", (client, contact)
        ).lastrowid
        self.db.execute(
            "INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto) "
            "VALUES (?, ?, 'email', '2026-11-18', ?)", (client, entry, text)
        )
        self.db.commit()
        return int(client)

    def harness(self) -> Harness:
        def transport(_url, body, _headers):
            self.calls.append(body)
            return {"choices": [{"message": {"content": "Resposta revisável"}}]}

        return Harness(self.db, CloudLLM(self.db, transport=transport, api_key="teste"))

    def test_name_in_topic_never_selects_another_client(self) -> None:
        bruno = self.client("Bruno", "Conversa sobre Ana Lima em novembro.")
        self.client("Ana Lima", "Conversa sobre Ana Lima de outro cliente.")
        hit = self.harness().search("Quando falei com Bruno sobre Ana Lima")
        self.assertEqual(hit.client_id, bruno)
        piece = json.loads(self.calls[0]["messages"][1]["content"])
        self.assertEqual(piece["trechos"], ["Conversa sobre Ana Lima em novembro."])

    def test_multiple_candidates_require_explicit_valid_selection(self) -> None:
        ana = self.client("Ana Lima", "O contrato da Ana Lima.")
        bruno = self.client("Bruno", "O contrato do Bruno.")
        unrelated = self.client("Carlos", "O contrato de Carlos.")
        harness = self.harness()
        phrase = "Quando falei com Ana Lima e Bruno sobre contrato"
        choice = harness.search(phrase)
        self.assertEqual({candidate.client_id for candidate in choice.candidates}, {ana, bruno})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rascunho").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            harness.search(phrase, client_id=unrelated)
        self.assertEqual(self.calls, [])
        hit = harness.search(phrase, client_id=bruno)
        self.assertEqual(hit.client_id, bruno)
        payload = json.loads(self.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["trechos"], ["O contrato do Bruno."])
        self.assertNotIn("O contrato da Ana Lima.", json.dumps(self.calls, ensure_ascii=False))

    def test_homonyms_are_distinguished_by_id(self) -> None:
        first = self.client("Ana Lima", "O contrato da primeira.")
        second = self.client("Ana Lima", "O contrato da segunda.")
        harness = self.harness()
        choice = harness.search("Quando falei com Ana Lima sobre contrato")
        self.assertEqual({candidate.client_id for candidate in choice.candidates}, {first, second})
        self.assertEqual(self.calls, [])
        hit = harness.search("Quando falei com Ana Lima sobre contrato", client_id=second)
        self.assertEqual(hit.client_id, second)
        piece = json.loads(self.calls[0]["messages"][1]["content"])
        self.assertEqual(piece["trechos"], ["O contrato da segunda."])

    def test_controller_presents_candidates_and_selected_id_reaches_harness(self) -> None:
        first = self.client("Ana Lima", "O contrato da primeira.")
        second = self.client("Ana Lima", "O contrato da segunda.")
        root = Path(self.tmp.name)
        (root / "modelo.json").write_text('{"api_key":"teste"}', encoding="utf-8")
        controller = Controller(root)
        phrase = "Quando falei com Ana Lima sobre contrato"
        self.assertEqual(controller.search(phrase), 0)
        self.assertEqual(controller.searchCandidates.rowCount(), 2)
        self.assertEqual(
            {row["clientId"] for row in controller._search_candidates._rows}, {first, second}
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rascunho").fetchone()[0], 0)
        calls = []

        def transport(_url, body, _headers):
            calls.append(body)
            return {"choices": [{"message": {"content": "Resposta revisável"}}]}

        class ImmediateThread:
            def __init__(self, *, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with patch("flow.controller.threading.Thread", ImmediateThread), patch(
            "flow.llm.post_chat", transport
        ):
            controller.selectSearchCandidate(phrase, second)
        piece = json.loads(calls[0]["messages"][1]["content"])
        self.assertEqual(piece["trechos"], ["O contrato da segunda."])
        self.assertEqual(controller.searchCandidates.rowCount(), 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rascunho").fetchone()[0], 1)
        controller.close()
