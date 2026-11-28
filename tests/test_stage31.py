"""Etapa 31: o que chega arquivado e o que sai enviado alimentam o índice do cliente."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flow.classify import Classifier, ReadTool
from flow.controller import Controller
from flow.send import record_delivery
from flow.store import add_entry_trecho, open_database, search_trechos_do_cliente


def _client(connection, name: str) -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')",
        (name,),
    ).lastrowid


def _contact(connection, client_id: int | None, value: str) -> int:
    return connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
        (client_id, value),
    ).lastrowid


def _process(connection, client_id: int, title: str) -> int:
    return connection.execute(
        'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
        (client_id, f"000{client_id}-11.2024.8.26.0100", title),
    ).lastrowid


def _entry(connection, contact_id: int, text: str, state: str = "nova") -> int:
    return connection.execute(
        """
        INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado, canal)
        VALUES (NULL, ?, '2026-11-18T09:00:00-03:00', ?, ?, 'whatsapp')
        """,
        (contact_id, text, state),
    ).lastrowid


def _trechos(connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT cliente_id, entrada_id, texto FROM trecho ORDER BY id"
        )
    ]


def _model(text: str):
    def transport(_url, _body, _headers):
        return {"choices": [{"message": {"content": text}}]}

    return transport


class TrechoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = open_database(self.data)
        self.bruno = _client(self.db, "Bruno Dias")
        self.ana = _client(self.db, "Ana Lima")
        _process(self.db, self.bruno, "Rescisão")
        self.phone = _contact(self.db, self.bruno, "+5511987654321")
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_archived_entry_is_searchable_only_by_its_client(self) -> None:
        entry = _entry(self.db, self.phone, "Doutor, e a rescisão do contrato?")
        self.assertEqual(Classifier(ReadTool(self.db)).classify(entry), "arquivada")
        self.db.commit()
        self.assertEqual(
            _trechos(self.db), [(self.bruno, entry, "Doutor, e a rescisão do contrato?")]
        )
        self.assertEqual(len(search_trechos_do_cliente(self.db, self.bruno, "rescisão")), 1)
        self.assertEqual(search_trechos_do_cliente(self.db, self.ana, "rescisão"), [])

    def test_pending_entry_has_no_trecho_until_indicated(self) -> None:
        entry = _entry(self.db, self.phone, "Sou a Maria, do celular do meu marido.")
        self.assertEqual(Classifier(ReadTool(self.db)).classify(entry), "nome diferente")
        self.db.commit()
        self.assertEqual(_trechos(self.db), [])
        controller = Controller(self.data)
        try:
            self.assertTrue(controller.indicate(entry, self.ana, 0))
        finally:
            controller.close()
        self.assertEqual(
            _trechos(self.db), [(self.ana, entry, "Sou a Maria, do celular do meu marido.")]
        )

    def test_same_entry_is_not_indexed_twice(self) -> None:
        entry = _entry(self.db, self.phone, "rescisão assinada")
        Classifier(ReadTool(self.db)).classify(entry)
        self.db.execute("UPDATE entrada SET estado = 'nova' WHERE id = ?", (entry,))
        Classifier(ReadTool(self.db)).classify(entry)
        self.assertFalse(add_entry_trecho(self.db, entry))
        self.db.commit()
        self.assertEqual(len(_trechos(self.db)), 1)

    def test_sent_text_becomes_trecho_of_the_approved_client(self) -> None:
        entry = _entry(self.db, self.phone, "rescisão?", "arquivada")
        self.db.execute("UPDATE entrada SET cliente_id = ? WHERE id = ?", (self.bruno, entry))
        draft = self.db.execute(
            """
            INSERT INTO rascunho (
                entrada_id, texto, estado, texto_aprovado, destino_aprovado,
                canal_aprovado, cliente_entrada_aprovado, cliente_contato_aprovado
            ) VALUES (?, 'rascunho vivo', 'em curso', 'Bruno, a rescisão saiu ontem.',
                      '+5511987654321', 'whatsapp', ?, ?)
            """,
            (entry, self.bruno, self.bruno),
        ).lastrowid
        self.db.commit()
        record_delivery(self.db, draft, "whatsapp")
        texts = [row[2] for row in _trechos(self.db)]
        self.assertIn("Bruno, a rescisão saiu ontem.", texts)
        self.assertNotIn("rascunho vivo", texts)
        self.assertEqual(len(search_trechos_do_cliente(self.db, self.bruno, "saiu ontem")), 1)
        self.assertEqual(search_trechos_do_cliente(self.db, self.ana, "saiu ontem"), [])

    def test_cycle_dispatch_also_indexes_sent_text(self) -> None:
        entry = _entry(self.db, self.phone, "rescisão?", "arquivada")
        self.db.execute("UPDATE entrada SET cliente_id = ? WHERE id = ?", (self.bruno, entry))
        self.db.execute(
            """
            INSERT INTO rascunho (
                entrada_id, texto, estado, texto_aprovado, destino_aprovado,
                canal_aprovado, cliente_entrada_aprovado, cliente_contato_aprovado
            ) VALUES (?, 'Bruno, protocolado.', 'aprovado', 'Bruno, protocolado.',
                      '+5511987654321', 'whatsapp', ?, ?)
            """,
            (entry, self.bruno, self.bruno),
        )
        self.db.commit()
        controller = Controller(self.data)
        try:
            queue = controller.run_cycle(
                datetime(2026, 11, 19, 8, 30, tzinfo=timezone(timedelta(hours=-3))),
                lambda *_args: True,
            )
        finally:
            controller.close()
        self.assertEqual(len(queue.sent), 1)
        self.assertIn("Bruno, protocolado.", [row[2] for row in _trechos(self.db)])

    def test_search_finds_indexed_history_end_to_end(self) -> None:
        entry = _entry(self.db, self.phone, "A rescisão foi homologada?")
        Classifier(ReadTool(self.db)).classify(entry)
        other_phone = _contact(self.db, self.ana, "+5511900000000")
        other = _entry(self.db, other_phone, "rescisão da Ana", "arquivada")
        self.db.execute("UPDATE entrada SET cliente_id = ? WHERE id = ?", (self.ana, other))
        add_entry_trecho(self.db, other)
        self.db.commit()
        (self.data / "modelo.json").write_text(json.dumps({"api_key": "k"}), encoding="utf-8")
        seen: list[dict] = []

        def transport(url, body, headers):
            seen.append(json.loads(body["messages"][1]["content"]))
            return _model("Bruno, a homologação saiu.")(url, body, headers)

        controller = Controller(self.data)
        try:
            draft = controller.search(
                "Quando falei com o Bruno sobre rescisão", transport=transport
            )
        finally:
            controller.close()
        self.assertGreater(draft, 0)
        self.assertEqual(seen[0]["trechos"], ["A rescisão foi homologada?"])
        self.assertNotIn("rescisão da Ana", json.dumps(seen, ensure_ascii=False))


class DraftContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_database(Path(self.tmp.name))
        self.bruno = _client(self.db, "Bruno Dias")
        self.ana = _client(self.db, "Ana Lima")
        phone = _contact(self.db, self.bruno, "+5511987654321")
        self.entry = _entry(self.db, phone, "base", "arquivada")
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _add(self, client: int, text: str, when: str = "2026-11-01T09:00:00") -> None:
        from flow.store import add_trecho

        add_trecho(self.db, client, self.entry, "whatsapp", when, text)
        self.db.commit()

    def _texts(self, client: int, message: str, limit: int = 5) -> list[str]:
        from flow.store import search_trechos_relevantes

        return [row["texto"] for row in search_trechos_relevantes(self.db, client, message, limit)]

    def test_any_relevant_word_brings_history(self) -> None:
        self._add(self.bruno, "A audiência da rescisão ficou para março.")
        self._add(self.bruno, "Mandei a procuração assinada.")
        self._add(self.bruno, "Bom dia")
        found = self._texts(self.bruno, "Doutor, alguma novidade da rescisão?")
        self.assertEqual(found, ["A audiência da rescisão ficou para março."])

    def test_more_relevant_first_and_limited(self) -> None:
        self._add(self.bruno, "rescisão", "2026-11-01T09:00:00")
        self._add(self.bruno, "rescisão do contrato de trabalho", "2026-11-02T09:00:00")
        for day in range(3, 10):
            self._add(self.bruno, f"contrato número {day}", f"2026-11-0{day}T09:00:00")
        found = self._texts(self.bruno, "rescisão do contrato", limit=5)
        self.assertEqual(len(found), 5)
        # bm25: a palavra rara pesa mais que a comum; os dois trechos com ela vêm antes.
        self.assertEqual(set(found[:2]), {"rescisão", "rescisão do contrato de trabalho"})
        self.assertTrue(all(text.startswith("contrato número") for text in found[2:]))

    def test_own_message_and_other_client_stay_out(self) -> None:
        message = "Doutor, e a rescisão?"
        self._add(self.bruno, message)
        self._add(self.ana, "SEGREDO-ANA rescisão")
        self.assertEqual(self._texts(self.bruno, message), [])

    def test_fts_syntax_in_message_does_not_break(self) -> None:
        self._add(self.bruno, "rescisão NOT pago")
        found = self._texts(self.bruno, 'rescisão "NOT" AND OR (pago) NEAR* -x')
        self.assertEqual(found, ["rescisão NOT pago"])

    def test_draft_payload_uses_relevant_history_and_search_keeps_all_words(self) -> None:
        from flow.llm import CloudLLM

        self._add(self.bruno, "A rescisão foi protocolada.")
        seen: list[dict] = []

        def transport(_url, body, _headers):
            seen.append(json.loads(body["messages"][1]["content"]))
            return {"choices": [{"message": {"content": "ok"}}]}

        llm = CloudLLM(self.db, transport=transport, api_key="k")
        message = "E a rescisão, doutor, saiu o acordo?"
        llm.complete("rascunho", client_id=self.bruno, message=message, query=message)
        llm.complete("busca", client_id=self.bruno, message=message, query="rescisão acordo")
        self.assertEqual(seen[0]["trechos"], ["A rescisão foi protocolada."])
        self.assertEqual(seen[1]["trechos"], [])


class BackfillTest(unittest.TestCase):
    def test_backfill_runs_once_and_does_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = open_database(data)
            client = _client(db, "Bruno Dias")
            phone = _contact(db, client, "+5511987654321")
            filed = _entry(db, phone, "contrato assinado", "arquivada")
            db.execute("UPDATE entrada SET cliente_id = ? WHERE id = ?", (client, filed))
            _entry(db, phone, "ainda pendente", "pendente")
            draft = db.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado, texto_aprovado,
                                      cliente_entrada_aprovado)
                VALUES (?, 'vivo', 'enviado', 'Bruno, recebido.', ?)
                """,
                (filed, client),
            ).lastrowid
            db.execute(
                "INSERT INTO envio (rascunho_id, enviado_em, canal) "
                "VALUES (?, '2026-11-19T10:00:00-03:00', 'whatsapp')",
                (draft,),
            )
            db.execute("DELETE FROM store_meta WHERE key = 'trecho_backfill_v1'")
            db.commit()
            db.close()

            for _ in range(2):
                db = open_database(data)
                rows = _trechos(db)
                db.close()
                self.assertEqual(
                    rows,
                    [(client, filed, "contrato assinado"), (client, filed, "Bruno, recebido.")],
                )

    def test_new_database_gets_the_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp))
            found = db.execute(
                "SELECT 1 FROM store_meta WHERE key = 'trecho_backfill_v1'"
            ).fetchone()
            db.close()
        self.assertIsNotNone(found)


if __name__ == "__main__":
    unittest.main()
