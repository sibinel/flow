"""Etapa 32: IMAP lê só o que é novo, e e-mail só de HTML vira texto."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.mail import ImapReader, _cursor_key, _load_cursor, _since, html_text
from flow.store import open_database


def _mail(number: int, *, html: str | None = None, text: str | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = "Ana Lima <ana.lima@email.com>"
    message["To"] = "advogado@email.com"
    message["Message-ID"] = f"<m{number}@flow>"
    message["Date"] = "Wed, 25 Nov 2026 10:00:00 -0300"
    if text is not None:
        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    else:
        message.set_content(f"mensagem {number}")
    return message.as_bytes()


class FakeBox:
    """Caixa IMAP em memória. Registra cada busca e cada download."""

    def __init__(self, validity: int = 10) -> None:
        self.validity: int | None = validity
        self.mail: dict[int, bytes] = {}
        self.searches: list[tuple] = []
        self.fetched: list[int] = []
        self.refuse: set[int] = set()

    def __call__(self, host, port):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def login(self, user, password) -> None:
        return None

    def select(self, mailbox, readonly=False):
        return "OK", [str(len(self.mail)).encode()]

    def response(self, name):
        if name == "UIDVALIDITY" and self.validity is not None:
            return name, [str(self.validity).encode()]
        return name, [None]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searches.append(args)
            if args[0] == "SINCE":
                found = sorted(self.mail)
            else:
                start = int(args[1].split(":")[0])
                found = [uid for uid in sorted(self.mail) if uid >= start]
                if not found and self.mail:
                    found = [max(self.mail)]  # "N:*" devolve a última mesmo abaixo de N
            return "OK", [" ".join(str(uid) for uid in found).encode()]
        if command == "FETCH":
            uid = int(args[0])
            self.fetched.append(uid)
            if uid in self.refuse:
                return "NO", [None]
            return "OK", [(f"{uid} (UID {uid} RFC822 {{1}}".encode(), self.mail[uid]), b")"]
        raise AssertionError(command)


class IncrementalImapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = open_database(self.data)
        self.box = FakeBox()
        self.key = _cursor_key("imap.example", "advogado@email.com")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _ingest(self, commit: bool = True):
        with patch("flow.mail._imap", new=self.box):
            result = ImapReader(self.db, self.data).ingest(
                "imap.example", 993, "advogado@email.com", "x"
            )
        if commit:
            self.db.commit()
        return result

    def _entries(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]

    def test_first_read_uses_recent_days_then_only_new_uids(self) -> None:
        self.box.mail = {3: _mail(3), 4: _mail(4)}
        first = self._ingest()
        self.assertEqual(self.box.searches[0], ("SINCE", _since()))
        self.assertEqual(len(first), 2)
        self.assertEqual(_load_cursor(self.db, self.key), {"uidvalidity": 10, "uid": 4})

        self.box.fetched.clear()
        second = self._ingest()
        self.assertEqual(self.box.searches[1], ("UID", "5:*"))
        self.assertEqual(self.box.fetched, [])
        self.assertEqual(list(second), [])

        self.box.mail[7] = _mail(7)
        third = self._ingest()
        self.assertEqual(self.box.fetched, [7])
        self.assertEqual(len(third), 1)
        self.assertEqual(self._entries(), 3)

    def test_since_date_is_seven_days_back_in_imap_format(self) -> None:
        self.assertEqual(_since(date(2026, 11, 28)), "21-Nov-2026")
        self.assertEqual(_since(date(2026, 1, 3)), "27-Dec-2025")

    def test_new_uidvalidity_restarts_without_duplicates(self) -> None:
        self.box.mail = {1: _mail(1), 2: _mail(2)}
        self._ingest()
        self.box.validity = 99
        self.box.mail = {50: _mail(1), 51: _mail(2), 52: _mail(3)}
        result = self._ingest()
        self.assertEqual(self.box.searches[-1][0], "SINCE")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.duplicates, 2)
        self.assertEqual(self._entries(), 3)
        self.assertEqual(_load_cursor(self.db, self.key), {"uidvalidity": 99, "uid": 52})

    def test_persistence_failure_holds_cursor_before_it(self) -> None:
        self.box.mail = {1: _mail(1), 2: _mail(2), 3: _mail(3)}
        original = ImapReader._save

        def flaky(reader, raw):
            if b"<m2@flow>" in raw:
                raise OSError("disco cheio")
            return original(reader, raw)

        with patch.object(ImapReader, "_save", flaky):
            result = self._ingest()
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(_load_cursor(self.db, self.key)["uid"], 1)

        self.box.fetched.clear()
        retry = self._ingest()
        self.assertEqual(self.box.fetched, [2, 3])
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry.duplicates, 1)
        self.assertEqual(self._entries(), 3)
        self.assertEqual(_load_cursor(self.db, self.key)["uid"], 3)

    def test_fetch_refusal_stops_the_batch(self) -> None:
        self.box.mail = {1: _mail(1), 2: _mail(2), 3: _mail(3)}
        self.box.refuse = {2}
        self._ingest()
        self.assertEqual(self.box.fetched, [1, 2])
        self.assertEqual(_load_cursor(self.db, self.key)["uid"], 1)
        self.box.refuse = set()
        self._ingest()
        self.assertEqual(self._entries(), 3)

    def test_rollback_also_discards_cursor(self) -> None:
        self.box.mail = {1: _mail(1)}
        self._ingest(commit=False)
        self.db.rollback()
        self.assertIsNone(_load_cursor(self.db, self.key))
        self.assertEqual(self._entries(), 0)

    def test_missing_uidvalidity_stores_nothing(self) -> None:
        self.box.validity = None
        self.box.mail = {1: _mail(1)}
        with self.assertRaises(RuntimeError):
            self._ingest()
        self.assertEqual(self._entries(), 0)


class HtmlBodyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = open_database(self.data)
        client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        ).lastrowid
        self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
            (client,),
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _store(self, raw: bytes):
        with patch("flow.mail._fetch", return_value=[raw]):
            ImapReader(self.db, self.data).ingest("h", 1, "advogado@email.com", "x")
        self.db.commit()
        entry = self.db.execute("SELECT id, texto, estado FROM entrada").fetchone()
        reasons = [
            row["motivo"]
            for row in self.db.execute(
                "SELECT motivo FROM pendencia WHERE entrada_id = ?", (entry["id"],)
            )
        ]
        return entry, reasons

    def test_html_only_mail_becomes_text(self) -> None:
        html = (
            "<html><head><title>T</title><style>p{color:red}</style></head>"
            "<body><p>Doutor,</p><p>segue o <b>contrato</b> assinado.</p>"
            "<script>alert('x')</script></body></html>"
        )
        entry, reasons = self._store(_mail(1, html=html))
        self.assertEqual(entry["texto"], "Doutor,\nsegue o contrato assinado.")
        self.assertEqual(entry["estado"], "nova")
        self.assertNotIn("mídia não baixada", reasons)

    def test_plain_text_wins_over_html_alternative(self) -> None:
        entry, _reasons = self._store(_mail(1, text="texto puro", html="<p>versão html</p>"))
        self.assertEqual(entry["texto"], "texto puro")

    def test_html_text_drops_hidden_content_and_decodes_entities(self) -> None:
        self.assertEqual(
            html_text("<div>A&amp;B</div><style>x</style><script>y</script><br>fim"),
            "A&B\nfim",
        )


if __name__ == "__main__":
    unittest.main()
