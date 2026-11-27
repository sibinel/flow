"""Etapa 23: falha antes do envio volta para aprovado. Depois, fica em curso."""

from __future__ import annotations

import json
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import package_dir, whatsapp_session
from flow.store import open_database


def _approved(controller: Controller) -> int:
    db = open_database(controller.database_path.parent)
    client = db.execute(
        "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
    ).lastrowid
    contact = db.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
        (client,),
    ).lastrowid
    entry = db.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, recebido_em, texto, estado
        ) VALUES (?, ?, '2026-11-23T07:00:00', 'contrato', 'arquivada')
        """,
        (client, contact),
    ).lastrowid
    draft = db.execute(
        """
        INSERT INTO rascunho (entrada_id, texto, estado)
        VALUES (?, 'Ana, confirmo.', 'aprovado')
        """,
        (entry,),
    ).lastrowid
    db.commit()
    db.close()
    return int(draft)


def _state(root: Path, draft: int) -> str:
    db = open_database(root)
    state = db.execute(
        "SELECT estado FROM rascunho WHERE id = ?",
        (draft,),
    ).fetchone()["estado"]
    sent = db.execute("SELECT COUNT(*) FROM envio").fetchone()[0]
    db.close()
    if sent:
        raise AssertionError("gravou envio")
    return state


class CertainDeliveryFailureTest(unittest.TestCase):
    def test_opening_and_login_return_to_approved(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            draft = _approved(controller)

            class Closed:
                def __init__(self, *args, **kwargs) -> None:
                    raise TimeoutError("abrir")

            with patch("flow.send.smtplib.SMTP_SSL", Closed):
                self.assertFalse(
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            self.assertEqual(_state(root, draft), "aprovado")

            class Denied:
                def __init__(self, *args, **kwargs) -> None:
                    return None

                def login(self, user: str, password: str) -> None:
                    raise smtplib.SMTPAuthenticationError(535, b"nao")

                def quit(self) -> None:
                    return None

            with patch("flow.send.smtplib.SMTP_SSL", Denied):
                self.assertFalse(
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            self.assertEqual(_state(root, draft), "aprovado")
            controller.close()

    def test_a_failure_after_the_server_was_asked_stays_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            draft = _approved(controller)

            class After:
                def __init__(self, *args, **kwargs) -> None:
                    return None

                def login(self, user: str, password: str) -> None:
                    return None

                def send_message(self, message) -> None:
                    raise smtplib.SMTPDataError(451, b"depois")

                def quit(self) -> None:
                    return None

            with patch("flow.send.smtplib.SMTP_SSL", After):
                with self.assertRaises(smtplib.SMTPDataError):
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
            self.assertEqual(_state(root, draft), "em curso")
            controller.close()

    def test_whatsapp_connect_returns_and_a_handed_send_stays(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (client,),
            ).lastrowid
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'oi', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, ok.', 'aprovado')",
                (entry,),
            ).lastrowid
            db.commit()
            db.close()
            session = whatsapp_session(root)
            session.mkdir(parents=True)
            (session / "creds.json").write_text(
                json.dumps({"registered": True}),
                encoding="utf-8",
            )

            self.assertFalse(
                controller.send_approved_whatsapp(
                    int(draft),
                    lambda *_args: {"sent": False, "handed": False},
                )
            )
            self.assertEqual(_state(root, int(draft)), "aprovado")
            self.assertFalse(
                controller.send_approved_whatsapp(
                    int(draft),
                    lambda *_args: {"sent": False, "handed": True},
                )
            )
            self.assertEqual(_state(root, int(draft)), "em curso")
            bridge = (package_dir() / "baileys" / "bridge.mjs").read_text(encoding="utf-8")
            self.assertIn("handed: false", bridge)
            self.assertIn("handed: true", bridge)
            controller.close()
