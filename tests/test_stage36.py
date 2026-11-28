"""Etapa 36: a resposta por e-mail sai com assunto, na mesma conversa, e cifrada."""

from __future__ import annotations

import smtplib
import sqlite3
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.mail import ImapReader
from flow.send import SmtpSender
from flow.store import open_database, reply_subject


def _seed(connection, *, subject="Contrato", message_id="<abc@cliente.com>", kind="email") -> int:
    client = connection.execute(
        "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
    ).lastrowid
    value = "ana.lima@email.com" if kind == "email" else "+5511987654321"
    contact = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, ?, ?)",
        (client, kind, value),
    ).lastrowid
    entry = connection.execute(
        """
        INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado,
                             mensagem_id, canal, conta, assunto)
        VALUES (?, ?, '2026-11-20T07:00:00-03:00', 'Segue o contrato', 'arquivada',
                ?, ?, 'advogado@email.com', ?)
        """,
        (client, contact, message_id, kind, subject),
    ).lastrowid
    draft = connection.execute(
        "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, recebido.', 'aguardando')",
        (entry,),
    ).lastrowid
    connection.commit()
    return int(draft)


def _row(controller: Controller, draft: int) -> dict:
    controller._reload()
    return next(row for row in controller._drafts._rows if row["draftId"] == draft)


class SubjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.controller = Controller(self.data)
        self.db = open_database(self.data)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_reply_subject_rules(self) -> None:
        self.assertEqual(reply_subject("Contrato"), "Re: Contrato")
        self.assertEqual(reply_subject("RE: Contrato"), "RE: Contrato")
        self.assertEqual(reply_subject("  Contrato \n assinado "), "Re: Contrato assinado")
        self.assertEqual(reply_subject(None), "")

    def test_mail_ingest_keeps_the_subject(self) -> None:
        message = EmailMessage()
        message["From"] = "ana.lima@email.com"
        message["Subject"] = "Contrato assinado"
        message["Message-ID"] = "<x1@cliente.com>"
        message["Date"] = "Fri, 20 Nov 2026 07:00:00 -0300"
        message.set_content("segue")
        with patch("flow.mail._fetch", return_value=[message.as_bytes()]):
            ImapReader(self.db, self.data).ingest("h", 1, "advogado@email.com", "x")
        self.db.commit()
        stored = self.db.execute("SELECT assunto FROM entrada").fetchone()["assunto"]
        self.assertEqual(stored, "Contrato assinado")

    def test_approval_freezes_the_subject_shown_in_the_queue(self) -> None:
        draft = _seed(self.db)
        row = _row(self.controller, draft)
        self.assertEqual(row["subject"], "Re: Contrato")
        self.assertTrue(self.controller.approve(draft, row["approvalVersion"]))
        frozen = self.db.execute(
            "SELECT assunto_aprovado FROM rascunho WHERE id = ?", (draft,)
        ).fetchone()["assunto_aprovado"]
        self.assertEqual(frozen, "Re: Contrato")

    def test_whatsapp_draft_has_no_subject(self) -> None:
        draft = _seed(self.db, kind="whatsapp", message_id="w1")
        self.assertEqual(_row(self.controller, draft)["subject"], "")

    def test_subject_change_after_display_invalidates_approval(self) -> None:
        draft = _seed(self.db)
        shown = _row(self.controller, draft)["approvalVersion"]
        self.db.execute("UPDATE entrada SET assunto = 'Outro assunto'")
        self.db.commit()
        self.assertFalse(self.controller.approve(draft, shown))
        state = self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?", (draft,)
        ).fetchone()["estado"]
        self.assertEqual(state, "aguardando")


class FakeSmtp:
    def __init__(self, trace: list, *, fail_tls: bool = False) -> None:
        self.trace = trace
        self.fail_tls = fail_tls
        self.sent: list[EmailMessage] = []

    def __call__(self, host, port, timeout=None, context=None):
        self.trace.append(("open", type(self).__name__, port, context is not None))
        return self

    def ehlo(self):
        self.trace.append(("ehlo",))

    def starttls(self, context=None):
        self.trace.append(("starttls", context is not None))
        if self.fail_tls:
            raise smtplib.SMTPNotSupportedError("sem STARTTLS")

    def login(self, user, password):
        self.trace.append(("login",))

    def send_message(self, message):
        self.trace.append(("send",))
        self.sent.append(message)
        return {}

    def quit(self):
        self.trace.append(("quit",))

    def close(self):
        self.trace.append(("close",))


class DeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.controller = Controller(self.data)
        self.db = open_database(self.data)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def _approved(self, **seed) -> int:
        draft = _seed(self.db, **seed)
        self.assertTrue(
            self.controller.approve(draft, _row(self.controller, draft)["approvalVersion"])
        )
        return draft

    def _send(self, draft: int, port: int, plain: FakeSmtp, ssl: FakeSmtp) -> bool:
        with (
            patch("flow.send.smtplib.SMTP", new=plain),
            patch("flow.send.smtplib.SMTP_SSL", new=ssl),
        ):
            return SmtpSender(self.db).send(draft, "smtp.example", port, "adv@flow", "x")

    def _state(self, draft: int) -> str:
        return self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?", (draft,)
        ).fetchone()["estado"]

    def test_reply_goes_in_the_same_thread(self) -> None:
        draft = self._approved()
        trace: list = []
        ssl = FakeSmtp(trace)
        self.assertTrue(self._send(draft, 465, FakeSmtp(trace), ssl))
        message = ssl.sent[0]
        self.assertEqual(message["Subject"], "Re: Contrato")
        self.assertEqual(message["In-Reply-To"], "<abc@cliente.com>")
        self.assertEqual(message["References"], "<abc@cliente.com>")

    def test_hashed_message_id_is_not_used_as_reply_reference(self) -> None:
        draft = self._approved(message_id="9f" * 32, subject=None)
        trace: list = []
        ssl = FakeSmtp(trace)
        self.assertTrue(self._send(draft, 465, FakeSmtp(trace), ssl))
        message = ssl.sent[0]
        self.assertIsNone(message["In-Reply-To"])
        self.assertIsNone(message["References"])
        self.assertIsNone(message["Subject"])

    def test_port_587_starts_tls_before_login(self) -> None:
        draft = self._approved()
        trace: list = []

        class Plain(FakeSmtp):
            pass

        class Ssl(FakeSmtp):
            pass

        self.assertTrue(self._send(draft, 587, Plain(trace), Ssl(trace)))
        names = [item[0] for item in trace]
        self.assertEqual(trace[0], ("open", "Plain", 587, False))
        self.assertLess(names.index("starttls"), names.index("login"))
        self.assertLess(names.index("login"), names.index("send"))
        self.assertIn(("starttls", True), trace)

    def test_port_465_uses_direct_ssl(self) -> None:
        draft = self._approved()
        trace: list = []

        class Plain(FakeSmtp):
            pass

        class Ssl(FakeSmtp):
            pass

        self.assertTrue(self._send(draft, 465, Plain(trace), Ssl(trace)))
        self.assertEqual(trace[0], ("open", "Ssl", 465, True))
        self.assertNotIn("starttls", [item[0] for item in trace])

    def test_failed_starttls_never_logs_in_and_releases_draft(self) -> None:
        draft = self._approved()
        trace: list = []
        self.assertFalse(self._send(draft, 587, FakeSmtp(trace, fail_tls=True), FakeSmtp(trace)))
        names = [item[0] for item in trace]
        self.assertNotIn("login", names)
        self.assertNotIn("send", names)
        self.assertEqual(self._state(draft), "aprovado")


class SubjectMigrationTest(unittest.TestCase):
    def test_email_approved_without_subject_goes_back_to_review_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            db = open_database(data)
            email_draft = _seed(db)
            whatsapp_draft = _seed(db, kind="whatsapp", message_id="w1")
            for draft, channel, state in (
                (email_draft, "email", "aprovado"),
                (whatsapp_draft, "whatsapp", "aprovado"),
            ):
                db.execute(
                    """
                    UPDATE rascunho
                    SET estado = ?, texto_aprovado = texto, destino_aprovado = 'x',
                        canal_aprovado = ?, cliente_entrada_aprovado = 1,
                        cliente_contato_aprovado = 1, assunto_aprovado = NULL
                    WHERE id = ?
                    """,
                    (state, channel, draft),
                )
            in_flight = _seed(db, message_id="<outra@cliente.com>")
            db.execute(
                "UPDATE rascunho SET estado = 'em curso', canal_aprovado = 'email' WHERE id = ?",
                (in_flight,),
            )
            db.execute("DELETE FROM store_meta WHERE key = 'email_subject_reset_v1'")
            db.commit()
            db.close()

            db = open_database(data)
            states = dict(db.execute("SELECT id, estado FROM rascunho").fetchall())
            db.execute(
                "UPDATE rascunho SET estado = 'aprovado', assunto_aprovado = NULL WHERE id = ?",
                (email_draft,),
            )
            db.commit()
            db.close()
            self.assertEqual(states[email_draft], "aguardando")
            self.assertEqual(states[whatsapp_draft], "aprovado")
            self.assertEqual(states[in_flight], "em curso")

            db = open_database(data)
            again = db.execute(
                "SELECT estado FROM rascunho WHERE id = ?", (email_draft,)
            ).fetchone()["estado"]
            db.close()
            self.assertEqual(again, "aprovado")

    def test_old_tables_get_subject_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            raw = sqlite3.connect(data / "flow.sqlite")
            raw.executescript(
                """
                CREATE TABLE entrada (id INTEGER PRIMARY KEY, cliente_id INTEGER,
                    contato_id INTEGER NOT NULL, processo_id INTEGER,
                    recebido_em TEXT NOT NULL, texto TEXT NOT NULL,
                    estado TEXT NOT NULL CHECK (estado IN ('nova', 'pendente', 'arquivada')));
                """
            )
            raw.commit()
            raw.close()
            db = open_database(data)
            entrada = {row[1] for row in db.execute("PRAGMA table_info(entrada)")}
            rascunho = {row[1] for row in db.execute("PRAGMA table_info(rascunho)")}
            db.close()
        self.assertIn("assunto", entrada)
        self.assertIn("assunto_aprovado", rascunho)


if __name__ == "__main__":
    unittest.main()
