"""Etapa 12: contato novo fica no lote, o id não repete, mídia faltante fica anotada."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.incoming import scoped_message_id
from flow.mail import ImapReader
from flow.paths import package_dir, whatsapp_session
from flow.store import open_database


def _raw(sender: str, subject: str, body: str, message_id: str) -> bytes:
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = "escritorio@flow.local"
    mail["Subject"] = subject
    mail["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    mail["Message-ID"] = message_id
    mail.set_content(body)
    return mail.as_bytes()


class IncomingBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)
        self.client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        ).lastrowid
        self.email = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', ?)",
            (self.client, "ana.lima@email.com"),
        ).lastrowid
        self.phone = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
            (self.client, "+5511987654321"),
        ).lastrowid
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_unknown_contact_stays_and_one_failure_does_not_drop_the_batch(self) -> None:
        before = self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0]
        known = _raw(
            "Ana Lima <ana.lima@email.com>",
            "Contrato",
            "Segue o contrato.",
            "<ana-1@flow>",
        )
        stranger = _raw(
            "Novo <novo@email.com>",
            "Oi",
            "Preciso de uma consulta.",
            "<novo-1@flow>",
        )
        broken = _raw(
            "Ana Lima <ana.lima@email.com>",
            "Falha",
            "QUEBRA-ESTA",
            "<quebra@flow>",
        )
        original = ImapReader._save

        def flaky(reader, raw: bytes):
            if b"QUEBRA-ESTA" in raw:
                raise RuntimeError("quebrada")
            return original(reader, raw)

        with (
            patch("flow.mail._fetch", return_value=[known, stranger, broken, known]),
            patch.object(ImapReader, "_save", flaky),
        ):
            ids = self.controller.ingest_mail("127.0.0.1", 143, "ana", "segredo")

        self.assertEqual(len(ids), 2)
        rows = self.db.execute(
            """
            SELECT entrada.texto, entrada.estado, entrada.cliente_id, entrada.mensagem_id,
                   contato.valor, contato.cliente_id AS dono, pendencia.motivo
            FROM entrada
            JOIN contato ON contato.id = entrada.contato_id
            LEFT JOIN pendencia ON pendencia.entrada_id = entrada.id
            ORDER BY entrada.id
            """
        ).fetchall()
        self.assertEqual([row["mensagem_id"] for row in rows], ["<ana-1@flow>", "<novo-1@flow>"])
        self.assertEqual(rows[0]["estado"], "nova")
        self.assertIsNone(rows[0]["cliente_id"])
        self.assertEqual(rows[0]["valor"], "ana.lima@email.com")
        self.assertEqual(rows[0]["dono"], self.client)
        self.assertIsNone(rows[0]["motivo"])
        self.assertEqual(rows[1]["estado"], "pendente")
        self.assertIsNone(rows[1]["cliente_id"])
        self.assertIsNone(rows[1]["dono"])
        self.assertEqual(rows[1]["valor"], "novo@email.com")
        self.assertEqual(rows[1]["motivo"], "contato novo")
        self.assertEqual(rows[1]["texto"], "Preciso de uma consulta.")
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM entrada WHERE mensagem_id = '<quebra@flow>'"
            ).fetchone()
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0], before)

        session = whatsapp_session(Path(self.controller.dataDirectory))
        session.mkdir()
        (session / "creds.json").write_text(
            json.dumps({"registered": True}),
            encoding="utf-8",
        )

        def runner(folder: Path, op: str, payload: dict) -> dict:
            self.assertEqual(op, "read")
            return {
                "messages": [
                    {
                        "id": "w-1",
                        "from": "+5511987654321",
                        "text": "primeiro",
                        "received_at": "2026-11-22T11:00:00-03:00",
                        "attachments": [],
                    },
                    {
                        "id": "w-2",
                        "from": "+5511987654321",
                        "text": "arquivo ruim",
                        "received_at": "2026-11-22T11:01:00-03:00",
                        "attachments": [{"name": "a.pdf", "data_b64": "!!!!"}],
                    },
                    {
                        "id": "w-3",
                        "from": "+5511900001111",
                        "text": "número novo",
                        "received_at": "2026-11-22T11:02:00-03:00",
                        "attachments": [],
                    },
                ]
            }

        wa_ids = self.controller.ingest_whatsapp(runner)
        self.assertEqual(len(wa_ids), 2)
        kept = self.db.execute(
            """
            SELECT entrada.texto, entrada.estado, entrada.cliente_id, entrada.mensagem_id,
                   pendencia.motivo
            FROM entrada
            LEFT JOIN pendencia ON pendencia.entrada_id = entrada.id
            WHERE entrada.mensagem_id IN (?, ?)
            ORDER BY entrada.id
            """,
            (
                scoped_message_id("whatsapp", "+5511987654321", "w-1"),
                scoped_message_id("whatsapp", "+5511900001111", "w-3"),
            ),
        ).fetchall()
        self.assertEqual(
            [row["mensagem_id"] for row in kept],
            [
                scoped_message_id("whatsapp", "+5511987654321", "w-1"),
                scoped_message_id("whatsapp", "+5511900001111", "w-3"),
            ],
        )
        self.assertEqual(kept[0]["estado"], "nova")
        self.assertEqual(kept[1]["texto"], "número novo")
        self.assertEqual(kept[1]["estado"], "pendente")
        self.assertIsNone(kept[1]["cliente_id"])
        self.assertEqual(kept[1]["motivo"], "contato novo")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0], before)

    def test_reread_keeps_the_stored_message_id(self) -> None:
        first = _raw("Ana Lima <ana.lima@email.com>", "Um", "Texto um.", "<um@flow>")
        second = _raw("Ana Lima <ana.lima@email.com>", "Dois", "Texto dois.", "<dois@flow>")
        with patch("flow.mail._fetch", return_value=[first, second]):
            created = self.controller.ingest_mail("127.0.0.1", 143, "ana", "segredo")
        self.assertEqual(len(created), 2)
        with patch("flow.mail._fetch", return_value=[first, second]):
            again = self.controller.ingest_mail("127.0.0.1", 143, "ana", "segredo")
        self.assertEqual(again, [])
        stored = self.db.execute(
            "SELECT mensagem_id FROM entrada ORDER BY id"
        ).fetchall()
        self.assertEqual([row["mensagem_id"] for row in stored], ["<um@flow>", "<dois@flow>"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 2)

        session = whatsapp_session(Path(self.controller.dataDirectory))
        session.mkdir()
        (session / "creds.json").write_text(json.dumps({"registered": True}), encoding="utf-8")
        payload = {
            "messages": [
                {
                    "id": "wa-9",
                    "from": "+5511987654321",
                    "text": "já li",
                    "received_at": "2026-11-22T12:00:00-03:00",
                    "attachments": [],
                }
            ]
        }

        def runner(folder: Path, op: str, body: dict) -> dict:
            return payload

        self.controller.ingest_whatsapp(runner)
        repeated = self.controller.ingest_whatsapp(runner)
        self.assertEqual(repeated, [])
        found = self.db.execute(
            "SELECT COUNT(*) FROM entrada WHERE mensagem_id = ?",
            (scoped_message_id("whatsapp", "+5511987654321", "wa-9"),),
        ).fetchone()[0]
        self.assertEqual(found, 1)

    def test_missing_media_is_noted_and_the_message_stays(self) -> None:
        session = whatsapp_session(Path(self.controller.dataDirectory))
        session.mkdir()
        (session / "creds.json").write_text(json.dumps({"registered": True}), encoding="utf-8")

        def runner(folder: Path, op: str, payload: dict) -> dict:
            return {
                "messages": [
                    {
                        "id": "mid-video",
                        "from": "+5511987654321",
                        "text": "olha o vídeo",
                        "received_at": "2026-11-22T13:00:00-03:00",
                        "attachments": [],
                        "media_note": "mídia não baixada: vídeo",
                    },
                    {
                        "id": "mid-grande",
                        "from": "+5511987654321",
                        "text": "",
                        "received_at": "2026-11-22T13:01:00-03:00",
                        "attachments": [],
                        "media_note": "mídia não baixada: imagem acima de 15 MB",
                    },
                    {
                        "id": "mid-erro",
                        "from": "+5511987654321",
                        "text": "",
                        "received_at": "2026-11-22T13:02:00-03:00",
                        "attachments": [],
                        "media_note": "mídia não baixada: documento",
                    },
                ]
            }

        ids = self.controller.ingest_whatsapp(runner)
        self.assertEqual(len(ids), 3)
        rows = {
            row["mensagem_id"]: row
            for row in self.db.execute(
                """
                SELECT entrada.mensagem_id, entrada.texto, entrada.estado, pendencia.motivo
                FROM entrada
                LEFT JOIN pendencia ON pendencia.entrada_id = entrada.id
                """
            )
        }
        video = scoped_message_id("whatsapp", "+5511987654321", "mid-video")
        grande = scoped_message_id("whatsapp", "+5511987654321", "mid-grande")
        erro = scoped_message_id("whatsapp", "+5511987654321", "mid-erro")
        self.assertEqual(rows[video]["estado"], "pendente")
        self.assertIn("olha o vídeo", rows[video]["texto"])
        self.assertIn("mídia não baixada: vídeo", rows[video]["texto"])
        self.assertEqual(rows[video]["motivo"], "mídia não baixada")
        self.assertEqual(rows[grande]["estado"], "pendente")
        self.assertEqual(rows[grande]["motivo"], "mídia não baixada")
        self.assertIn("acima de 15 MB", rows[grande]["texto"])
        self.assertEqual(rows[erro]["estado"], "pendente")
        self.assertEqual(rows[erro]["motivo"], "mídia não baixada")
        self.assertIn("documento", rows[erro]["texto"])

        def retry(folder: Path, op: str, payload: dict) -> dict:
            return {
                "messages": [
                    {
                        "id": "mid-video",
                        "from": "+5511987654321",
                        "text": "olha o vídeo",
                        "received_at": "2026-11-22T13:00:00-03:00",
                        "attachments": [
                            {
                                "name": "video.mp4",
                                "data_b64": base64.b64encode(b"video").decode("ascii"),
                            }
                        ],
                    },
                    {
                        "id": "mid-grande",
                        "from": "+5511987654321",
                        "text": "",
                        "received_at": "2026-11-22T13:01:00-03:00",
                        "attachments": [],
                        "media_note": "mídia não baixada: imagem acima de 15 MB",
                    },
                ]
            }

        again = self.controller.ingest_whatsapp(retry)
        self.assertEqual(again, [ids[0]])
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM entrada WHERE mensagem_id = ?", (video,)).fetchone()[0],
            1,
        )
        stored = self.db.execute(
            """
            SELECT entrada.texto, entrada.estado, pendencia.motivo, documento.nome
            FROM entrada
            LEFT JOIN pendencia ON pendencia.entrada_id = entrada.id
            LEFT JOIN documento ON documento.entrada_id = entrada.id
            WHERE entrada.mensagem_id = ?
            """,
            (video,),
        ).fetchone()
        self.assertEqual(stored["estado"], "nova")
        self.assertEqual(stored["texto"], "olha o vídeo")
        self.assertIsNone(stored["motivo"])
        self.assertEqual(stored["nome"], "video.mp4")
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM entrada WHERE mensagem_id = ?",
                (grande,),
            ).fetchone()[0],
            1,
        )

        bridge = (package_dir() / "baileys" / "bridge.mjs").read_text(encoding="utf-8")
        self.assertIn("media_note", bridge)
        self.assertIn("acima de 15 MB", bridge)
        self.assertIn("audioMessage", bridge)
        self.assertIn("stickerMessage", bridge)
        self.assertIn("cursor.json", bridge)
        self.assertIn("fetchMessageHistory", bridge)
        self.assertNotIn("if (!text && !attachment) continue", bridge)

    def test_cycle_keeps_the_batch_when_one_message_fails(self) -> None:
        path = Path(self.controller.dataDirectory) / "email.json"
        path.write_text(
            json.dumps(
                {
                    "host": "imap.example.com",
                    "port": 993,
                    "user": "ana@example.com",
                    "password": "segredo",
                }
            ),
            encoding="utf-8",
        )
        known = _raw(
            "Ana Lima <ana.lima@email.com>",
            "Contrato",
            "Segue o contrato.",
            "<ciclo-ana@flow>",
        )
        stranger = _raw(
            "Novo <novo@email.com>",
            "Oi",
            "Preciso de uma consulta.",
            "<ciclo-novo@flow>",
        )
        broken = _raw(
            "Ana Lima <ana.lima@email.com>",
            "Falha",
            "QUEBRA-CICLO",
            "<ciclo-quebra@flow>",
        )
        original = ImapReader._save

        def flaky(reader, raw: bytes):
            if b"QUEBRA-CICLO" in raw:
                raise RuntimeError("quebrada")
            return original(reader, raw)

        with (
            patch("flow.mail._fetch", return_value=[known, broken, stranger]),
            patch.object(ImapReader, "_save", flaky),
        ):
            opened = self.controller._tick(datetime(2026, 11, 23, 8, 0))

        self.assertTrue(opened.ran)
        ids = [
            row["mensagem_id"]
            for row in self.db.execute("SELECT mensagem_id FROM entrada ORDER BY id")
        ]
        self.assertEqual(ids, ["<ciclo-ana@flow>", "<ciclo-novo@flow>"])
        pending = self.db.execute(
            """
            SELECT pendencia.motivo
            FROM pendencia
            JOIN entrada ON entrada.id = pendencia.entrada_id
            WHERE entrada.mensagem_id = '<ciclo-novo@flow>'
            """
        ).fetchone()
        self.assertEqual(pending["motivo"], "contato novo")
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM entrada WHERE mensagem_id = '<ciclo-quebra@flow>'"
            ).fetchone()
        )
