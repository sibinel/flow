"""Etapa 30: contato novo vira cliente, prospecção ou pessoal."""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.incoming import open_entry
from flow.mail import ImapReader
from flow.paths import store_document, whatsapp_session
from flow.store import open_database
from flow.whatsapp import WhatsAppReader


def _client(connection, name: str) -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')",
        (name,),
    ).lastrowid


def _new_contact_entry(connection, value: str, text: str, kind: str = "whatsapp") -> int:
    entry = open_entry(
        connection,
        kind=kind,
        value=value,
        message_id=f"id-{text}",
        received_at="2026-11-18T12:00:00",
        text=text,
        has_text=True,
        has_file=False,
        account=kind if kind == "whatsapp" else "advogado@email.com",
    )
    connection.commit()
    return int(entry)


def _mail(sender: str, text: str, attachment: bytes | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "advogado@email.com"
    message["Message-ID"] = f"<{text}@x>"
    message["Date"] = "Wed, 18 Nov 2026 12:00:00 -0300"
    message.set_content(text)
    if attachment is not None:
        message.add_attachment(
            attachment, maintype="application", subtype="pdf", filename="contrato.pdf"
        )
    return message.as_bytes()


class NewContactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.controller = Controller(self.data)

    def tearDown(self) -> None:
        self.controller.close()
        self.tmp.cleanup()

    def _db(self):
        return open_database(self.data)

    def test_indicate_links_new_contact_so_next_message_is_known(self) -> None:
        with self._db() as connection:
            client = _client(connection, "Paula Reis")
            entry = _new_contact_entry(connection, "+5511911112222", "primeira")
        self.assertTrue(self.controller.indicate(entry, client, 0))
        with self._db() as connection:
            owner = connection.execute(
                "SELECT cliente_id FROM contato WHERE valor = '+5511911112222'"
            ).fetchone()["cliente_id"]
            second = _new_contact_entry(connection, "+5511911112222", "segunda")
            reasons = [
                row["motivo"]
                for row in connection.execute(
                    "SELECT motivo FROM pendencia WHERE entrada_id = ?", (second,)
                )
            ]
            state = connection.execute(
                "SELECT estado FROM entrada WHERE id = ?", (second,)
            ).fetchone()["estado"]
        self.assertEqual(owner, client)
        self.assertNotIn("contato novo", reasons)
        self.assertEqual(state, "nova")

    def test_indicate_keeps_owner_of_contact_that_already_has_one(self) -> None:
        with self._db() as connection:
            owner = _client(connection, "Bruno Dias")
            other = _client(connection, "Maria Dias")
            contact = connection.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', '+5511933334444')",
                (owner,),
            ).lastrowid
            entry = connection.execute(
                """
                INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado)
                VALUES (NULL, ?, '2026-11-18T12:00:00', 'Sou a Maria', 'pendente')
                """,
                (contact,),
            ).lastrowid
            connection.commit()
        self.assertTrue(self.controller.indicate(entry, other, 0))
        with self._db() as connection:
            kept = connection.execute(
                "SELECT cliente_id FROM contato WHERE id = ?", (contact,)
            ).fetchone()["cliente_id"]
        self.assertEqual(kept, owner)

    def test_create_client_validates_name_and_status(self) -> None:
        self.assertEqual(self.controller.createClient("   ", "cliente"), 0)
        self.assertEqual(self.controller.createClient("Ana", "pessoal"), 0)
        created = self.controller.createClient("  Joana   Prado ", "prospecção")
        self.assertGreater(created, 0)
        with self._db() as connection:
            row = connection.execute(
                "SELECT nome, status FROM cliente WHERE id = ?", (created,)
            ).fetchone()
        self.assertEqual((row["nome"], row["status"]), ("Joana Prado", "prospecção"))
        names = [
            self.controller.clients.data(self.controller.clients.index(i, 0), role)
            for i in range(self.controller.clients.rowCount())
            for role, name in self.controller.clients.roleNames().items()
            if bytes(name) == b"name"
        ]
        self.assertIn("Joana Prado", names)

    def test_mark_personal_deletes_pending_entries_and_files(self) -> None:
        docs = self.data / "documentos"
        with self._db() as connection:
            first = _new_contact_entry(connection, "+5511955556666", "oi")
            second = _new_contact_entry(connection, "+5511955556666", "foto")
            path = store_document(connection, docs, "foto.jpg", second, 1, b"jpg")
            connection.commit()
            connection.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'x', 'aguardando')",
                (first,),
            )
            connection.commit()
        self.assertTrue(path.is_file())
        self.assertTrue(self.controller.markPersonal(first))
        with self._db() as connection:
            entries = connection.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
            leftovers = sum(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("documento", "pendencia", "rascunho")
            )
            personal = connection.execute(
                "SELECT pessoal FROM contato WHERE valor = '+5511955556666'"
            ).fetchone()["pessoal"]
        self.assertEqual(entries, 0)
        self.assertEqual(leftovers, 0)
        self.assertEqual(personal, 1)
        self.assertFalse(path.exists())
        self.assertEqual(self.controller.pendings.rowCount(), 0)

    def test_mark_personal_refuses_contact_with_owner_or_sent_draft(self) -> None:
        with self._db() as connection:
            owner = _client(connection, "Bruno Dias")
            contact = connection.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', '+5511977778888')",
                (owner,),
            ).lastrowid
            owned = connection.execute(
                """
                INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado)
                VALUES (NULL, ?, '2026-11-18T12:00:00', 'Sou a Maria', 'pendente')
                """,
                (contact,),
            ).lastrowid
            stranger = _new_contact_entry(connection, "+5511900001111", "novo")
            connection.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'x', 'em curso')",
                (stranger,),
            )
            connection.commit()
        self.assertFalse(self.controller.markPersonal(owned))
        self.assertFalse(self.controller.markPersonal(stranger))
        with self._db() as connection:
            entries = connection.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
            personal = connection.execute(
                "SELECT COUNT(*) FROM contato WHERE pessoal = 1"
            ).fetchone()[0]
        self.assertEqual(entries, 2)
        self.assertEqual(personal, 0)

    def test_personal_email_is_not_stored_nor_its_attachment(self) -> None:
        with self._db() as connection:
            entry = _new_contact_entry(connection, "amigo@email.com", "churrasco", kind="email")
        self.assertTrue(self.controller.markPersonal(entry))
        with self._db() as connection:
            with patch(
                "flow.mail._fetch",
                return_value=[_mail("Amigo <AMIGO@email.com>", "sabado", b"%PDF")],
            ):
                result = ImapReader(connection, self.data).ingest("h", 1, "advogado@email.com", "x")
            connection.commit()
            entries = connection.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
            docs = connection.execute("SELECT COUNT(*) FROM documento").fetchone()[0]
        self.assertEqual(list(result), [])
        self.assertEqual(entries, 0)
        self.assertEqual(docs, 0)
        folder = self.data / "documentos"
        self.assertEqual(
            [item for item in folder.iterdir() if item.is_file()] if folder.exists() else [],
            [],
        )

    def test_personal_whatsapp_is_not_stored_nor_its_attachment(self) -> None:
        session = whatsapp_session(self.data)
        session.mkdir(parents=True)
        (session / "creds.json").write_text(json.dumps({"registered": True}), encoding="utf-8")
        with self._db() as connection:
            entry = _new_contact_entry(connection, "+5511922223333", "oi")
        self.assertTrue(self.controller.markPersonal(entry))

        def runner(_session, op, _payload):
            self.assertEqual(op, "read")
            return {
                "messages": [
                    {
                        "id": "w1",
                        "from": "+5511922223333",
                        "text": "foto da festa",
                        "received_at": "2026-11-18T15:00:00Z",
                        "attachments": [
                            {"name": "festa.jpg", "data_b64": base64.b64encode(b"jpg").decode()}
                        ],
                    }
                ]
            }

        with self._db() as connection:
            result = WhatsAppReader(connection, self.data, runner).ingest()
            connection.commit()
            entries = connection.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
            docs = connection.execute("SELECT COUNT(*) FROM documento").fetchone()[0]
        self.assertEqual(list(result), [])
        self.assertEqual(result.failures, [])
        self.assertEqual(entries, 0)
        self.assertEqual(docs, 0)

    def test_pending_reports_whether_contact_has_owner(self) -> None:
        with self._db() as connection:
            entry = _new_contact_entry(connection, "+5511944445555", "quem")
        self.controller._reload()
        row = next(r for r in self.controller._pendings._rows if r["entryId"] == entry)
        self.assertTrue(row["unowned"])


class PersonalMigrationTest(unittest.TestCase):
    def test_old_contact_table_gets_personal_flag_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            raw = sqlite3.connect(data / "flow.sqlite")
            raw.executescript(
                """
                CREATE TABLE cliente (id INTEGER PRIMARY KEY, nome TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('prospecção', 'cliente', 'novo')));
                CREATE TABLE contato (id INTEGER PRIMARY KEY,
                    cliente_id INTEGER REFERENCES cliente(id),
                    tipo TEXT NOT NULL CHECK (tipo IN ('whatsapp', 'email')),
                    valor TEXT NOT NULL);
                INSERT INTO cliente (id, nome, status) VALUES (1, 'Ana', 'cliente');
                INSERT INTO contato (id, cliente_id, tipo, valor) VALUES (1, 1, 'email', 'a@x');
                """
            )
            raw.commit()
            raw.close()
            for _ in range(2):
                connection = open_database(data)
                rows = connection.execute("SELECT id, valor, pessoal FROM contato").fetchall()
                connection.close()
                self.assertEqual([tuple(row) for row in rows], [(1, "a@x", 0)])


if __name__ == "__main__":
    unittest.main()
