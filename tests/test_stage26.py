"""Etapa 26: processo e cliente ambíguos não escolhem, e a chave fica visível."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flow.classify import Classifier, ReadTool
from flow.controller import Controller
from flow.store import open_database

PHRASE = "Quando falei com o João sobre rescisão"


class WholeProcessTest(unittest.TestCase):
    def test_a_piece_of_the_number_or_title_does_not_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            bruno = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
            ).lastrowid
            phone = db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (bruno,),
            ).lastrowid
            short = db.execute(
                'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
                (bruno, "1234", "casa"),
            ).lastrowid
            whole = db.execute(
                'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
                (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
            ).lastrowid
            piece = db.execute(
                """
                INSERT INTO entrada (
                    contato_id, recebido_em, texto, estado
                ) VALUES (?, '2026-11-23T07:00:00', ?, 'nova')
                """,
                (phone, "O processo 12345 trata do casamento."),
            ).lastrowid
            exact = db.execute(
                """
                INSERT INTO entrada (
                    contato_id, recebido_em, texto, estado
                ) VALUES (?, '2026-11-23T07:10:00', ?, 'nova')
                """,
                (phone, "Bruno, no processo 1001234-55.2024.8.26.0100."),
            ).lastrowid
            db.commit()
            classifier = Classifier(ReadTool(db))
            self.assertEqual(classifier.classify(int(piece)), "processo não citado")
            self.assertEqual(classifier.classify(int(exact)), "arquivada")
            filed = db.execute(
                "SELECT processo_id FROM entrada WHERE id = ?",
                (exact,),
            ).fetchone()
            missed = db.execute(
                "SELECT processo_id, estado FROM entrada WHERE id = ?",
                (piece,),
            ).fetchone()
            self.assertEqual(filed["processo_id"], whole)
            self.assertNotEqual(filed["processo_id"], short)
            self.assertIsNone(missed["processo_id"])
            self.assertEqual(missed["estado"], "pendente")
            db.close()
            controller.close()


class AmbiguousClientTest(unittest.TestCase):
    def test_two_names_of_the_same_size_do_not_draft(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            for nome in ("Ana Lima", "Ana Souza"):
                client = db.execute(
                    "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')",
                    (nome,),
                ).lastrowid
                phone = db.execute(
                    """
                    INSERT INTO contato (cliente_id, tipo, valor)
                    VALUES (?, 'whatsapp', ?)
                    """,
                    (client, f"+55119{client:08d}"),
                ).lastrowid
                entry = db.execute(
                    """
                    INSERT INTO entrada (
                        cliente_id, contato_id, recebido_em, texto, estado
                    ) VALUES (?, ?, '2026-11-18T10:00:00', 'fala da Ana', 'arquivada')
                    """,
                    (client, phone),
                ).lastrowid
                db.execute(
                    """
                    INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
                    VALUES (?, ?, 'whatsapp', '2026-11-18T10:00:00', 'fala da Ana')
                    """,
                    (client, entry),
                )
            db.commit()
            called: list[dict] = []

            def transport(_url, body, headers):
                called.append(headers)
                return {"choices": [{"message": {"content": "não deveria"}}]}

            draft_id = controller.search(
                "Quando falei com a Ana sobre rescisão",
                transport=transport,
            )
            self.assertEqual(draft_id, 0)
            self.assertEqual(called, [])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rascunho").fetchone()[0], 0)
            self.assertEqual(controller.notice, "cliente ambíguo")
            db.close()
            controller.close()


class ModelKeyTest(unittest.TestCase):
    def _ready(self, root: Path) -> Controller:
        controller = Controller(root)
        db = open_database(root)
        joao = db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('João', 'cliente')"
        ).lastrowid
        phone = db.execute(
            """
            INSERT INTO contato (cliente_id, tipo, valor)
            VALUES (?, 'whatsapp', '+5511987654321')
            """,
            (joao,),
        ).lastrowid
        entry = db.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, recebido_em, texto, estado
            ) VALUES (?, ?, '2026-11-18T10:00:00', 'fala da rescisão', 'arquivada')
            """,
            (joao, phone),
        ).lastrowid
        db.execute(
            """
            INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
            VALUES (?, ?, 'whatsapp', '2026-11-18T10:00:00', 'fala da rescisão')
            """,
            (joao, entry),
        )
        db.commit()
        db.close()
        return controller

    def test_without_a_key_the_call_stays_and_the_queue_shows_it(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = self._ready(root)
            called: list[str] = []

            def transport(*_args):
                called.append("saiu")
                return {"choices": [{"message": {"content": "não"}}]}

            self.assertEqual(controller.search(PHRASE, transport=transport), 0)
            self.assertEqual(called, [])
            self.assertEqual(controller.notice, "chave do modelo ausente")
            controller.close()

    def test_the_file_key_goes_in_the_request(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = self._ready(root)
            (root / "modelo.json").write_text(
                json.dumps({"api_key": "segredo"}),
                encoding="utf-8",
            )
            seen: list[dict] = []

            def transport(_url, _body, headers):
                seen.append(headers)
                return {"choices": [{"message": {"content": "A rescisão foi em novembro."}}]}

            draft_id = controller.search(PHRASE, transport=transport)
            self.assertGreater(draft_id, 0)
            self.assertEqual(seen[0]["Authorization"], "Bearer segredo")
            self.assertEqual(controller.notice, "")
            controller.close()
