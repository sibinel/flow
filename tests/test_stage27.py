"""Etapa 2: coleta parcial, anexos recuperáveis e contêiner MIME nomeado.

Contrato proposto: ingest retorna uma sequência de IDs, com ``saved`` contendo
os mesmos IDs, ``duplicates`` contando itens já existentes e ``failures``
contendo objetos com ``index`` (base zero no lote) e ``error``. A recuperação
é automática no commit, rollback e na abertura do banco; seu registro é privado.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.mail import ImapReader
from flow.paths import whatsapp_session
from flow.store import open_database
from flow.whatsapp import WhatsAppReader, discard_cursor


def _mail(message_id: str, attachment: bytes | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = "Ana <ana@example.com>"
    message["To"] = "conta@example.com"
    message["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    message["Message-ID"] = message_id
    message.set_content("Mensagem " + message_id)
    if attachment is not None:
        message.add_attachment(
            attachment, maintype="application", subtype="pdf", filename="arquivo.pdf"
        )
    return message.as_bytes()


def _ingest(db, root: Path, messages: list[bytes]):
    with patch("flow.mail._fetch", return_value=messages):
        return ImapReader(db, root).ingest("127.0.0.1", 143, "conta", "senha")


def _documents(db) -> dict[str, Path]:
    return {
        row["mensagem_id"]: Path(row["caminho"])
        for row in db.execute(
            "SELECT entrada.mensagem_id, documento.caminho FROM documento "
            "JOIN entrada ON entrada.id = documento.entrada_id"
        )
    }


def _interrupt_after_file(root: str) -> None:
    """Sai sem fechar nem confirmar a conexão, depois de gravar o anexo."""
    from flow.paths import store_document

    db = open_database(Path(root))
    db.execute("BEGIN")

    def interrupt(*args, **kwargs):
        store_document(*args, **kwargs)
        os._exit(73)

    with patch("flow.mail.store_document", interrupt):
        _ingest(db, Path(root), [_mail("<interrompida>", b"incompleto")])
    os._exit(74)


class BatchResultTest(unittest.TestCase):
    def test_middle_failure_is_reported_and_retry_deduplicates_successes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            messages = [_mail("<primeira>"), _mail("<falha>"), _mail("<ultima>")]
            original = ImapReader._save

            def flaky(reader, raw):
                result = original(reader, raw)
                if b"<falha>" in raw:
                    raise OSError("disco indisponível")
                return result

            with patch.object(ImapReader, "_save", flaky):
                first = _ingest(db, root, messages)
            db.commit()
            self.assertEqual(list(first), list(first.saved))
            self.assertEqual(len(first.saved), 2)
            self.assertEqual(first.duplicates, 0)
            self.assertEqual(len(first.failures), 1)
            self.assertEqual(first.failures[0].index, 1)
            self.assertIn("disco indisponível", str(first.failures[0].error))
            self.assertEqual(
                {row[0] for row in db.execute("SELECT mensagem_id FROM entrada")},
                {"<primeira>", "<ultima>"},
            )

            second = _ingest(db, root, messages)
            db.commit()
            self.assertEqual(list(second), list(second.saved))
            self.assertEqual(len(second.saved), 1)
            self.assertEqual(second.duplicates, 2)
            self.assertEqual(second.failures, [])
            self.assertEqual(
                [tuple(row) for row in db.execute(
                    "SELECT mensagem_id, COUNT(*) FROM entrada GROUP BY mensagem_id ORDER BY mensagem_id"
                )],
                [("<falha>", 1), ("<primeira>", 1), ("<ultima>", 1)],
            )
            db.close()

    def test_cycle_keeps_slot_open_after_item_failure_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            (root / "email.json").write_text(json.dumps({
                "host": "imap.example.com", "port": 993,
                "user": "conta", "password": "senha",
            }), encoding="utf-8")
            messages = [_mail("<primeira>"), _mail("<falha>"), _mail("<ultima>")]
            original = ImapReader._save

            def flaky(reader, raw):
                if b"<falha>" in raw:
                    raise OSError("falha ao persistir")
                return original(reader, raw)

            with patch("flow.mail._fetch", return_value=messages):
                with patch.object(ImapReader, "_save", flaky):
                    first = controller.run_cycle(datetime(2026, 11, 23, 8, 1))
                self.assertTrue(first.ran)
                self.assertNotIn("ultimo", json.loads((root / "ciclo.json").read_text()))
                self.assertIn("e-mail", controller.notice.lower())
                db = open_database(root)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 2)
                db.close()
                second = controller.run_cycle(datetime(2026, 11, 23, 8, 2))
                third = controller.run_cycle(datetime(2026, 11, 23, 8, 3))
            self.assertTrue(second.ran)
            self.assertFalse(third.ran)
            db = open_database(root)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 3)
            db.close()
            controller.close()

    def test_whatsapp_reports_item_failure_and_deduplicates_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session = whatsapp_session(root)
            session.mkdir(parents=True)
            (session / "creds.json").write_text('{"registered": true}', encoding="utf-8")
            db = open_database(root)
            messages = [
                {"id": item, "from": "+5511987654321", "text": item,
                 "received_at": "2026-11-18T10:00:00-03:00", "attachments": []}
                for item in ("primeira", "falha", "ultima")
            ]
            reader = WhatsAppReader(db, root, lambda *_args: {"messages": messages})
            original = reader._save

            def flaky(message):
                result = original(message)
                if message["id"] == "falha":
                    raise OSError("falha whatsapp")
                return result

            with patch.object(reader, "_save", flaky):
                first = reader.ingest()
            db.commit()
            self.assertEqual(list(first), list(first.saved))
            self.assertEqual(len(first.saved), 2)
            self.assertEqual(first.duplicates, 0)
            self.assertEqual(len(first.failures), 1)
            self.assertEqual(first.failures[0].index, 1)
            self.assertIn("falha whatsapp", str(first.failures[0].error))

            second = reader.ingest()
            db.commit()
            self.assertEqual(len(second.saved), 1)
            self.assertEqual(second.duplicates, 2)
            self.assertEqual(second.failures, [])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 3)
            discard_cursor(db)
            db.close()


class AttachmentRecoveryTest(unittest.TestCase):
    def test_existing_file_at_link_time_is_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            original_link = os.link
            collided: list[Path] = []

            def collision(stage, target):
                target = Path(target)
                target.write_bytes(b"arquivo alheio")
                collided.append(target)
                return original_link(stage, target)

            with patch("flow.paths.os.link", collision):
                result = _ingest(db, root, [_mail("<colisao>", b"anexo")])
            db.commit()
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(collided[0].read_bytes(), b"arquivo alheio")
            db.close()

    def test_external_rollback_cleans_only_uncommitted_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            _ingest(db, root, [_mail("<confirmada>", b"confirmado")])
            db.commit()
            committed = _documents(db)["<confirmada>"]
            unrelated = root / "documentos" / "alheio.pdf"
            unrelated.write_bytes(b"alheio")
            db.execute("BEGIN")
            _ingest(db, root, [_mail("<desfeita>", b"reverter")])
            uncommitted = _documents(db)["<desfeita>"]
            db.rollback()
            self.assertNotIn("<desfeita>", _documents(db))
            self.assertFalse(uncommitted.exists())
            self.assertEqual(committed.read_bytes(), b"confirmado")
            self.assertEqual(unrelated.read_bytes(), b"alheio")
            db.close()

    def test_partial_write_is_cleaned_and_retry_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            _ingest(db, root, [_mail("<confirmada>", b"confirmado")])
            db.commit()
            committed = _documents(db)["<confirmada>"]
            unrelated = root / "documentos" / "alheio.pdf"
            unrelated.write_bytes(b"alheio")
            from flow.paths import _write_staged

            partial_paths: list[Path] = []

            def partial(path, data):
                if data == b"novo anexo":
                    partial_paths.append(path)
                    _write_staged(path, data[:4])
                    raise OSError("escrita parcial")
                return _write_staged(path, data)

            with patch("flow.paths._write_staged", partial):
                failed = _ingest(db, root, [_mail("<nova>", b"novo anexo")])
            db.commit()
            self.assertEqual(len(partial_paths), 1, "injeção de falha deve atingir a escrita")
            self.assertEqual(len(failed.failures), 1)
            self.assertFalse(partial_paths[0].exists())
            self.assertNotIn("<nova>", _documents(db))
            retried = _ingest(db, root, [_mail("<nova>", b"novo anexo")])
            db.commit()
            self.assertEqual(len(retried.saved), 1)
            self.assertEqual(_documents(db)["<nova>"].read_bytes(), b"novo anexo")
            self.assertEqual(committed.read_bytes(), b"confirmado")
            self.assertEqual(unrelated.read_bytes(), b"alheio")
            db.close()

    def test_interruption_is_recovered_on_open_without_touching_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            _ingest(db, root, [_mail("<confirmada>", b"confirmado")])
            db.commit()
            committed = _documents(db)["<confirmada>"]
            unrelated = root / "documentos" / "alheio.pdf"
            unrelated.write_bytes(b"alheio")
            db.close()

            process = multiprocessing.get_context("spawn").Process(
                target=_interrupt_after_file, args=(str(root),)
            )
            process.start()
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join()
                self.fail("processo de interrupção não terminou")
            self.assertEqual(process.exitcode, 73)
            db = open_database(root)
            self.assertEqual(set(_documents(db)), {"<confirmada>"})
            self.assertEqual(committed.read_bytes(), b"confirmado")
            self.assertEqual(unrelated.read_bytes(), b"alheio")
            files = [path for path in (root / "documentos").rglob("*") if path.is_file()]
            self.assertNotIn(b"incompleto", [path.read_bytes() for path in files])
            retry = _ingest(db, root, [_mail("<interrompida>", b"incompleto")])
            db.commit()
            self.assertEqual(len(retry.saved), 1)
            self.assertEqual(_documents(db)["<interrompida>"].read_bytes(), b"incompleto")
            db.close()


class MultipartTest(unittest.TestCase):
    def test_named_multipart_container_is_not_an_empty_attachment(self) -> None:
        message = EmailMessage()
        message["From"] = "Ana <ana@example.com>"
        message["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
        message["Message-ID"] = "<multipart>"
        message.set_content("Corpo real")
        container = EmailMessage()
        container.make_mixed()
        container.add_header("Content-Disposition", "attachment", filename="pacote.mime")
        container.add_attachment(b"pdf real", maintype="application", subtype="pdf", filename="real.pdf")
        container.add_attachment(b"texto real", maintype="text", subtype="plain", filename="real.txt")
        message.make_mixed()
        message.attach(container)

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            result = _ingest(db, root, [message.as_bytes()])
            db.commit()
            self.assertEqual(len(result), 1)
            entry = db.execute("SELECT texto FROM entrada WHERE id = ?", (result[0],)).fetchone()
            self.assertEqual(entry["texto"], "Corpo real")
            docs = db.execute("SELECT nome, caminho FROM documento ORDER BY nome").fetchall()
            self.assertEqual(
                {row["nome"]: Path(row["caminho"]).read_bytes() for row in docs},
                {"real.pdf": b"pdf real", "real.txt": b"texto real"},
            )
            db.close()