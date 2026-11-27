"""Etapa 10: o ciclo da manhã monta a fila. Fora do horário, nada sai."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.cycle import CycleConfig
from flow.paths import package_dir
from flow.store import open_database


def _model(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _client(connection, name: str) -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')",
        (name,),
    ).lastrowid


def _contact(connection, client_id: int, kind: str, value: str) -> int:
    return connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, ?, ?)",
        (client_id, kind, value),
    ).lastrowid


def _entry(connection, client_id, contact_id: int, text: str, when: str, state: str) -> int:
    return connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (?, ?, NULL, ?, ?, ?)
        """,
        (client_id, contact_id, when, text, state),
    ).lastrowid


class DayCycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)
        (Path(self.controller.dataDirectory) / "modelo.json").write_text(
            json.dumps({"api_key": "teste"}),
            encoding="utf-8",
        )
        self.calls: list[tuple[int, str]] = []

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def _dispatch(self, draft_id: int, channel: str) -> bool:
        self.calls.append((draft_id, channel))
        return True

    def _config(self) -> dict:
        path = Path(self.controller.dataDirectory) / "ciclo.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_cycle_queues_followup_and_filing_and_holds_the_rest(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        email = _contact(self.db, bruno, "email", "bruno.dias@email.com")
        phone = _contact(self.db, bruno, "whatsapp", "+5511987654321")
        process = self.db.execute(
            'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
            (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
        ).lastrowid
        approved_entry = _entry(
            self.db,
            bruno,
            email,
            "Contrato recebido ontem.",
            "2026-11-22T09:00:00",
            "arquivada",
        )
        approved = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Bruno, confirmo o recebimento.', 'aprovado')
            """,
            (approved_entry,),
        ).lastrowid
        clean_text = "Bruno, segue a petição da rescisão."
        clean = _entry(self.db, None, phone, clean_text, "2026-11-23T07:00:00", "nova")
        filing_text = "Segue o contrato assinado para arquivar."
        filing = _entry(self.db, None, phone, filing_text, "2026-11-23T07:30:00", "nova")
        docs = Path(self.controller.dataDirectory) / "documentos"
        docs.mkdir()
        stored = docs / "contrato.pdf"
        stored.write_bytes(b"%PDF-1.4 contrato")
        self.db.execute(
            "INSERT INTO documento (entrada_id, caminho, nome) VALUES (?, ?, 'contrato.pdf')",
            (filing, str(stored)),
        )
        later_text = "Mensagem da tarde, sem processo."
        later = _entry(self.db, None, phone, later_text, "2026-11-23T09:30:00", "nova")
        self.db.commit()

        early = datetime(2026, 11, 23, 7, 0)
        held = self.controller.run_cycle(early, self._dispatch)
        self.assertTrue(held.ran)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT estado FROM entrada WHERE id = ?", (clean,)).fetchone()["estado"],
            "nova",
        )
        saved = self._config()
        self.assertEqual(saved["quantidade"], 1)
        self.assertEqual(saved["horarios"], ["08:00"])
        self.assertEqual(saved["ultimo"], "2026-11-22T08:00:00")

        morning = datetime(2026, 11, 23, 8, 0)
        with patch("flow.llm.post_chat", return_value=_model("Pode protocolar a rescisão.")):
            result = self.controller.run_cycle(morning, self._dispatch)
        fila = result.fila()
        follow = self.db.execute(
            "SELECT id, texto, estado FROM rascunho WHERE entrada_id = ?",
            (clean,),
        ).fetchone()
        pending = self.db.execute(
            """
            SELECT pendencia.id, pendencia.motivo, entrada.estado,
                   entrada.cliente_id, entrada.processo_id
            FROM pendencia
            JOIN entrada ON entrada.id = pendencia.entrada_id
            WHERE pendencia.entrada_id = ?
            """,
            (filing,),
        ).fetchone()
        document = self.db.execute(
            "SELECT caminho, nome FROM documento WHERE entrada_id = ?",
            (filing,),
        ).fetchone()

        self.assertTrue(result.ran)
        self.assertEqual(set(fila), {"rascunho", "pendencia"})
        self.assertEqual(follow["estado"], "aguardando")
        self.assertEqual(follow["texto"], "Pode protocolar a rescisão.")
        self.assertNotIn(clean_text, follow["texto"])
        self.assertNotIn(later_text, follow["texto"])
        self.assertIn(follow["id"], fila["rascunho"])
        self.assertEqual(pending["motivo"], "processo não citado")
        self.assertEqual(pending["estado"], "pendente")
        self.assertIsNone(pending["cliente_id"])
        self.assertIsNone(pending["processo_id"])
        self.assertIn(pending["id"], fila["pendencia"])
        self.assertEqual(document["nome"], "contrato.pdf")
        self.assertEqual(Path(document["caminho"]).read_bytes(), b"%PDF-1.4 contrato")
        for draft_id in fila["rascunho"]:
            state = self.db.execute(
                "SELECT estado FROM rascunho WHERE id = ?",
                (draft_id,),
            ).fetchone()["estado"]
            self.assertEqual(state, "aguardando")
        for pending_id in fila["pendencia"]:
            self.assertIsNotNone(
                self.db.execute(
                    "SELECT 1 FROM pendencia WHERE id = ?",
                    (pending_id,),
                ).fetchone()
            )
        self.assertNotIn(approved, fila["rascunho"])
        self.assertNotIn(follow["id"], result.sent)
        self.assertEqual(self.calls, [(approved, "email")])
        self.assertEqual(
            self.db.execute(
                "SELECT estado FROM rascunho WHERE id = ?",
                (approved,),
            ).fetchone()["estado"],
            "enviado",
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)
        later_row = self.db.execute(
            "SELECT estado FROM entrada WHERE id = ?",
            (later,),
        ).fetchone()
        self.assertEqual(later_row["estado"], "nova")
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM rascunho WHERE entrada_id = ?",
                (later,),
            ).fetchone()
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM pendencia WHERE entrada_id = ?",
                (later,),
            ).fetchone()
        )

        self.assertTrue(self.controller.indicate(filing, bruno, process))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)
        self.assertTrue(self.controller.approve(follow["id"]))
        self.calls.clear()
        again = self.controller.run_cycle(morning, self._dispatch)
        self.assertFalse(again.ran)
        self.assertEqual(self.calls, [])
        self.assertEqual(
            self.db.execute(
                "SELECT estado FROM rascunho WHERE id = ?",
                (follow["id"],),
            ).fetchone()["estado"],
            "aprovado",
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)

        nxt = self.controller.run_cycle(datetime(2026, 11, 24, 8, 0), self._dispatch)
        self.assertTrue(nxt.ran)
        self.assertEqual(set(nxt.fila()), {"rascunho", "pendencia"})
        self.assertEqual(self.calls, [(follow["id"], "whatsapp")])
        self.assertNotIn(follow["id"], nxt.fila()["rascunho"])
        self.assertEqual(
            self.db.execute(
                "SELECT estado FROM rascunho WHERE id = ?",
                (follow["id"],),
            ).fetchone()["estado"],
            "enviado",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT estado FROM entrada WHERE id = ?",
                (later,),
            ).fetchone()["estado"],
            "pendente",
        )
        waited = self.db.execute(
            "SELECT id FROM pendencia WHERE entrada_id = ?",
            (later,),
        ).fetchone()["id"]
        self.assertIn(waited, nxt.fila()["pendencia"])

    def test_only_the_configured_time_sends(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        email = _contact(self.db, bruno, "email", "bruno.dias@email.com")
        entry = _entry(
            self.db,
            bruno,
            email,
            "Pode confirmar.",
            "2026-11-22T09:00:00",
            "arquivada",
        )
        draft = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Bruno, confirmo.', 'aprovado')
            """,
            (entry,),
        ).lastrowid
        self.db.commit()
        config = CycleConfig(quantity=1, times=("07:15", "18:00"))

        early = self.controller.run_cycle(datetime(2026, 11, 23, 7, 0), self._dispatch, config)
        self.assertTrue(early.ran)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

        opened = self.controller.run_cycle(datetime(2026, 11, 23, 8, 0), self._dispatch, config)
        self.assertTrue(opened.ran)
        self.assertEqual(self.calls, [(draft, "email")])
        self.assertEqual(set(opened.fila()), {"rascunho", "pendencia"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)
        self.assertEqual(self._config()["quantidade"], 1)
        self.assertEqual(self._config()["horarios"], ["07:15", "18:00"])
        self.assertEqual(self._config()["ultimo"], "2026-11-23T07:15:00")

        again = self.controller.run_cycle(datetime(2026, 11, 23, 18, 0), self._dispatch, config)
        self.assertFalse(again.ran)

    def test_false_does_not_record_and_an_existing_envio_is_not_repeated(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        email = _contact(self.db, bruno, "email", "bruno.dias@email.com")
        entry = _entry(
            self.db,
            bruno,
            email,
            "Pode confirmar.",
            "2026-11-22T09:00:00",
            "arquivada",
        )
        draft = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Bruno, confirmo.', 'aprovado')
            """,
            (entry,),
        ).lastrowid
        self.db.commit()
        calls: list[object] = []

        def refuse(draft_id: int, channel: str) -> bool:
            calls.append(("no", draft_id, channel))
            return False

        refused = self.controller.run_cycle(datetime(2026, 11, 23, 8, 0), refuse)
        self.assertTrue(refused.ran)
        self.assertEqual(refused.sent, ())
        self.assertEqual(calls, [("no", draft, "email")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
            "aprovado",
        )

        def accept(draft_id: int, channel: str) -> bool:
            calls.append(("yes", draft_id, channel))
            return True

        accepted = self.controller.run_cycle(datetime(2026, 11, 24, 8, 0), accept)
        self.assertEqual(accepted.sent, (draft,))
        self.assertEqual(calls, [("no", draft, "email"), ("yes", draft, "email")])
        rows = self.db.execute(
            "SELECT canal FROM envio WHERE rascunho_id = ?",
            (draft,),
        ).fetchall()
        self.assertEqual([row["canal"] for row in rows], ["email"])
        self.assertEqual(
            self.db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
            "enviado",
        )

        self.db.execute(
            "UPDATE rascunho SET estado = 'aprovado' WHERE id = ?",
            (draft,),
        )
        self.db.commit()

        def again(draft_id: int, channel: str) -> bool:
            calls.append(("again", draft_id, channel))
            return True

        repeated = self.controller.run_cycle(datetime(2026, 11, 25, 8, 0), again)
        self.assertEqual(repeated.sent, ())
        self.assertEqual(calls, [("no", draft, "email"), ("yes", draft, "email")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)


class ModelFollowupTest(unittest.TestCase):
    def test_followup_uses_only_that_client_and_a_failure_sends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            (Path(name) / "modelo.json").write_text(
                json.dumps({"api_key": "teste"}),
                encoding="utf-8",
            )
            db = open_database(Path(name))
            bruno = _client(db, "Bruno Dias")
            ana = _client(db, "Ana Lima")
            phone = _contact(db, bruno, "whatsapp", "+5511987654321")
            db.execute(
                'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
                (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
            )
            current = _entry(
                db,
                None,
                phone,
                "Bruno, segue a petição da rescisão.",
                "2026-11-23T07:00:00",
                "nova",
            )
            history = _entry(
                db, bruno, phone, "conversa antiga", "2026-11-01T09:00:00", "arquivada"
            )
            db.execute(
                """
                INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
                VALUES (?, ?, 'whatsapp', '2026-11-01T09:00:00', ?)
                """,
                (bruno, history, "Bruno segue a petição da rescisão"),
            )
            ana_entry = _entry(
                db, ana, phone, "segredo da Ana", "2026-11-01T10:00:00", "arquivada"
            )
            db.execute(
                """
                INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
                VALUES (?, ?, 'whatsapp', '2026-11-01T10:00:00', ?)
                """,
                (ana, ana_entry, "SEGREDO-ANA Bruno segue a petição da rescisão"),
            )
            approved_entry = _entry(
                db,
                bruno,
                phone,
                "Já aprovado.",
                "2026-11-22T09:00:00",
                "arquivada",
            )
            approved = db.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado)
                VALUES (?, 'Bruno, confirmo.', 'aprovado')
                """,
                (approved_entry,),
            ).lastrowid
            db.commit()
            seen: list[dict] = []

            def transport(_url, body, _headers):
                piece = json.loads(body["messages"][1]["content"])
                seen.append(piece)
                raise RuntimeError("modelo caiu")

            with patch("flow.llm.post_chat", transport):
                failed = controller.run_cycle(datetime(2026, 11, 23, 8, 0), lambda *_args: True)
            self.assertEqual(seen[0]["tarefa"], "rascunho")
            self.assertEqual(seen[0]["mensagem"], "Bruno, segue a petição da rescisão.")
            self.assertEqual(seen[0]["trechos"], ["Bruno segue a petição da rescisão"])
            self.assertNotIn("SEGREDO-ANA", json.dumps(seen, ensure_ascii=False))
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM rascunho WHERE entrada_id = ?",
                    (current,),
                ).fetchone()
            )
            self.assertEqual(failed.sent, (approved,))
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM envio WHERE rascunho_id = ?",
                    (approved,),
                ).fetchone()[0],
                1,
            )
            db.close()
            controller.close()


class WindowDoesNotRunTheCycleTest(unittest.TestCase):
    def test_window_does_not_run_the_cycle(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("run_cycle", "ciclo.json"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
