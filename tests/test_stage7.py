"""Etapa 7: sem a sessão, WhatsApp para até um QR novo."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from flow.classify import Classifier, ReadTool
from flow.controller import Controller
from flow.paths import package_dir, whatsapp_session
from flow.store import open_database
from flow.whatsapp import SessionRequired, WhatsAppReader, WhatsAppSender, session_ready
import flow.whatsapp as whatsapp


def _pdf() -> str:
    return base64.b64encode(b"%PDF-1.4 procuracao").decode("ascii")


class WhatsAppSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.controller = Controller(self.data)
        self.db = open_database(self.controller.database_path.parent)
        self._invoke = whatsapp.invoke
        whatsapp.invoke = self._explode
        self.client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
        ).lastrowid
        self.contact = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
            (self.client, "+5511987654321"),
        ).lastrowid
        self.entry = self.db.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, recebido_em, texto, estado
            ) VALUES (?, ?, '2026-11-22T10:00:00', 'petição', 'nova')
            """,
            (self.client, self.contact),
        ).lastrowid
        self.draft = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Bruno, a petição da rescisão está pronta. Posso enviar?', 'aguardando')
            """,
            (self.entry,),
        ).lastrowid
        self.db.commit()

    def tearDown(self) -> None:
        whatsapp.invoke = self._invoke
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def _explode(self, *args, **kwargs) -> dict:
        raise AssertionError("ponte do Baileys chamada")

    def test_without_session_read_and_send_stop_until_a_new_qr(self) -> None:
        session = whatsapp_session(self.data)
        docs = self.data / "documentos"
        pkg = package_dir().resolve()
        self.assertFalse(session.exists())
        self.assertFalse(session_ready(session))
        self.assertNotIn("documentos", session.parts)
        self.assertFalse(session == pkg or pkg in session.parents)
        self.assertNotEqual(session, self.controller.database_path)

        reader = WhatsAppReader(self.db, self.data)
        self.assertEqual(reader.tool_names(), ("leitura",))
        self.assertFalse(hasattr(reader, "send"))
        with self.assertRaises(SessionRequired):
            self.controller.ingest_whatsapp()

        held = WhatsAppSender(self.db, self.data)
        self.assertEqual(held.tool_names(), ())
        self.assertFalse(held.send(self.draft))
        self.assertEqual(held.tool_names(), ())
        self.assertTrue(self.controller.approve(self.draft))
        self.assertEqual(Classifier(ReadTool(self.db)).tool_names(), ("leitura",))
        self.assertNotIn("envio", Classifier(ReadTool(self.db)).tool_names())
        with self.assertRaises(SessionRequired):
            self.controller.send_approved_whatsapp(self.draft)
        self.assertEqual(held.tool_names(), ())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertFalse(docs.exists())

        session.mkdir()
        (session / "creds.json").write_text(
            json.dumps({"registered": False}),
            encoding="utf-8",
        )
        self.assertFalse(session_ready(session))
        with self.assertRaises(SessionRequired):
            reader.ingest()
        with self.assertRaises(SessionRequired):
            held.send(self.draft)

        docs.mkdir()
        (docs / "creds.json").write_text(
            json.dumps({"registered": True}),
            encoding="utf-8",
        )
        self.assertFalse(session_ready(whatsapp_session(self.data)))
        with self.assertRaises(SessionRequired):
            self.controller.ingest_whatsapp()
        self.assertTrue((docs / "creds.json").is_file())
        self.assertFalse(pkg in (docs / "creds.json").resolve().parents)
        self.assertFalse((pkg / "creds.json").exists())
        self.assertFalse((pkg / "whatsapp-session").exists())

    def test_session_stays_outside_documents_and_package(self) -> None:
        docs = (self.data / "documentos").resolve()
        pkg = package_dir().resolve()
        ops: list[str] = []
        sent: list[dict] = []

        def runner(session: Path, op: str, payload: dict) -> dict:
            resolved = session.resolve()
            self.assertEqual(resolved, whatsapp_session(self.data).resolve())
            self.assertNotIn("documentos", resolved.parts)
            self.assertFalse(resolved == docs or docs in resolved.parents)
            self.assertFalse(resolved == pkg or pkg in resolved.parents)
            self.assertNotEqual(resolved, self.controller.database_path)
            ops.append(op)
            if op == "pair":
                (session / "creds.json").write_text(
                    json.dumps({"registered": True}),
                    encoding="utf-8",
                )
                return {"ready": True}
            if op == "read":
                return {
                    "messages": [
                        {
                            "from": "+55 11 98765-4321",
                            "text": "Segue a procuração.",
                            "received_at": "2026-11-22T11:00:00-03:00",
                            "attachments": [
                                {"name": "procuracao.pdf", "data_b64": _pdf()},
                            ],
                        },
                        {
                            "from": "+5511900001111",
                            "text": "mensagem pessoal",
                            "received_at": "2026-11-22T11:05:00-03:00",
                            "attachments": [],
                        },
                    ]
                }
            if op == "send":
                sent.append(payload)
                return {"sent": True}
            raise AssertionError(op)

        with self.assertRaises(ValueError):
            whatsapp_session(pkg / "dados")
        with self.assertRaises(ValueError):
            whatsapp_session(self.data / "documentos")

        before = self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0]
        self.controller.pair_whatsapp(runner)
        session = whatsapp_session(self.data)
        self.assertTrue(session_ready(session))
        self.assertEqual(ops, ["pair"])
        self.assertTrue((session / "creds.json").is_file())
        self.assertFalse((docs / "creds.json").exists())
        self.assertFalse(pkg in (session / "creds.json").resolve().parents)

        entry_ids = self.controller.ingest_whatsapp(runner)
        self.assertEqual(ops, ["pair", "read"])
        self.assertEqual(len(entry_ids), 1)
        stored = self.db.execute(
            "SELECT cliente_id, contato_id, processo_id, texto, estado FROM entrada WHERE id = ?",
            (entry_ids[0],),
        ).fetchone()
        document = self.db.execute(
            "SELECT caminho, nome FROM documento WHERE entrada_id = ?",
            (entry_ids[0],),
        ).fetchone()
        saved = Path(document["caminho"])
        self.assertEqual(stored["texto"], "Segue a procuração.")
        self.assertEqual(stored["estado"], "nova")
        self.assertIsNone(stored["cliente_id"])
        self.assertIsNone(stored["processo_id"])
        self.assertEqual(stored["contato_id"], self.contact)
        self.assertEqual(document["nome"], "procuracao.pdf")
        self.assertEqual(saved.read_bytes(), b"%PDF-1.4 procuracao")
        self.assertTrue(docs in saved.resolve().parents)
        self.assertFalse(session.resolve() in saved.resolve().parents)
        self.assertFalse(pkg in saved.resolve().parents)
        self.assertFalse((session / "procuracao.pdf").exists())
        personal = self.db.execute(
            "SELECT COUNT(*) FROM entrada WHERE texto = 'mensagem pessoal'"
        ).fetchone()[0]
        self.assertEqual(personal, 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0], before)
        self.assertNotIn("send", ops)

        reader = WhatsAppReader(self.db, self.data, runner)
        self.assertEqual(reader.tool_names(), ("leitura",))
        self.assertFalse(hasattr(reader, "send"))
        held = WhatsAppSender(self.db, self.data, runner)
        self.assertEqual(held.tool_names(), ())
        self.assertFalse(held.send(self.draft))
        self.assertEqual(held.tool_names(), ())
        self.assertNotIn("send", ops)

        self.assertTrue(self.controller.approve(self.draft))
        self.assertEqual(Classifier(ReadTool(self.db)).tool_names(), ("leitura",))
        self.assertNotIn("envio", Classifier(ReadTool(self.db)).tool_names())
        self.assertTrue(held.send(self.draft))
        self.assertEqual(held.tool_names(), ("envio",))
        self.assertEqual(ops, ["pair", "read", "send"])
        self.assertEqual(
            sent,
            [{"to": "+5511987654321", "text": "Bruno, a petição da rescisão está pronta. Posso enviar?"}],
        )
        self.db.commit()
        recorded = self.db.execute(
            "SELECT canal FROM envio WHERE rascunho_id = ?",
            (self.draft,),
        ).fetchone()
        state = self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?",
            (self.draft,),
        ).fetchone()["estado"]
        self.assertEqual(recorded["canal"], "whatsapp")
        self.assertEqual(state, "enviado")
        self.assertFalse((docs / "creds.json").exists())
        self.assertTrue((session / "creds.json").is_file())

        spec = pkg.parents[1] / "flow.spec"
        bundled = spec.read_text(encoding="utf-8")
        self.assertNotIn("whatsapp-session", bundled)
        self.assertNotIn("creds.json", bundled)
        bridge = (pkg / "baileys" / "bridge.mjs").read_text(encoding="utf-8").lower()
        meta = json.loads((pkg / "baileys" / "package.json").read_text(encoding="utf-8"))
        self.assertIn("@whiskeysockets/baileys", bridge)
        self.assertIn("@whiskeysockets/baileys", meta["dependencies"])
        for banned in ("chromium", "puppeteer", "playwright", "graph.facebook.com"):
            self.assertNotIn(banned, bridge)
            self.assertNotIn(banned, meta["dependencies"])

    def test_same_attachment_name_keeps_both_files(self) -> None:
        other = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        ).lastrowid
        self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
            (other, "+5511911112222"),
        )
        self.db.commit()
        session = whatsapp_session(self.data)
        session.mkdir()
        (session / "creds.json").write_text(
            json.dumps({"registered": True}),
            encoding="utf-8",
        )
        docs = self.data / "documentos"

        def runner(session_path: Path, op: str, payload: dict) -> dict:
            self.assertEqual(op, "read")
            return {
                "messages": [
                    {
                        "from": "+5511987654321",
                        "text": "dois contratos",
                        "received_at": "2026-11-22T11:00:00-03:00",
                        "attachments": [
                            {"name": "contrato.pdf", "data_b64": base64.b64encode(b"um").decode()},
                            {
                                "name": "../segredo/contrato.pdf",
                                "data_b64": base64.b64encode(b"dois").decode(),
                            },
                        ],
                    },
                    {
                        "from": "+5511911112222",
                        "text": "outro cliente",
                        "received_at": "2026-11-22T11:05:00-03:00",
                        "attachments": [
                            {"name": "contrato.pdf", "data_b64": base64.b64encode(b"tres").decode()},
                        ],
                    },
                ]
            }

        entry_ids = self.controller.ingest_whatsapp(runner)
        self.assertEqual(len(entry_ids), 2)
        rows = self.db.execute(
            "SELECT entrada_id, caminho, nome FROM documento ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["nome"] for row in rows], ["contrato.pdf"] * 3)
        self.assertEqual(
            [row["entrada_id"] for row in rows],
            [entry_ids[0], entry_ids[0], entry_ids[1]],
        )
        stored = [Path(row["caminho"]) for row in rows]
        self.assertEqual(
            [path.name for path in stored],
            [
                f"contrato-{entry_ids[0]}-1.pdf",
                f"contrato-{entry_ids[0]}-2.pdf",
                f"contrato-{entry_ids[1]}-1.pdf",
            ],
        )
        for path in stored:
            self.assertEqual(path.parent.resolve(), docs.resolve())
            self.assertNotIn("segredo", path.parts)
        self.assertEqual(stored[0].read_bytes(), b"um")
        self.assertEqual(stored[1].read_bytes(), b"dois")
        self.assertEqual(stored[2].read_bytes(), b"tres")
        self.assertFalse((docs / "contrato.pdf").exists())
        self.assertFalse((docs / "segredo").exists())

    def test_window_does_not_open_whatsapp(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("baileys", "ingest_whatsapp", "send_approved_whatsapp", "pair_whatsapp"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
