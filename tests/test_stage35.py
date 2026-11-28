"""Etapa 35: celular brasileiro com e sem o nono dígito é o mesmo contato."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from flow.incoming import contact_for, open_entry, phone_variants, same_phone
from flow.notice import read_notice
from flow.paths import package_dir, whatsapp_session
from flow.store import open_database
from flow.whatsapp import WhatsAppSender

_CASES = (
    ("+5511987654321", "+551187654321", True),
    ("5511987654321", "551187654321", True),
    ("+55 (21) 99876-5432", "+552199876543", False),  # números diferentes, mesmo com prefixo parecido
    ("+5521998765432", "+552198765432", True),
    ("+551132345678", "+5511932345678", False),  # fixo não ganha o nono dígito
    ("+5511923456789", "+551123456789", False),  # sem o 9, viraria número de fixo
    ("+14155551234", "+141555512340", False),  # outro país
    ("+5511987654321", "+5511987654321", True),
)


class PhoneVariantsTest(unittest.TestCase):
    def test_equivalence_cases(self) -> None:
        for left, right, expected in _CASES:
            with self.subTest(left=left, right=right):
                self.assertEqual(same_phone(left, right), expected)
                self.assertEqual(same_phone(right, left), expected)

    def test_variants_of_mobile_and_fixed(self) -> None:
        self.assertEqual(phone_variants("+5511987654321"), {"5511987654321", "551187654321"})
        self.assertEqual(phone_variants("+551187654321"), {"551187654321", "5511987654321"})
        self.assertEqual(phone_variants("+551132345678"), {"551132345678"})
        self.assertEqual(phone_variants(""), set())

    @unittest.skipUnless(shutil.which("node"), "Node ausente")
    def test_bridge_uses_the_same_rule(self) -> None:
        source = (package_dir() / "baileys" / "bridge.mjs").read_text(encoding="utf-8")
        function = re.search(r"function phoneVariants\(digits\) \{.*?\n\}\n", source, re.S).group(0)
        numbers = sorted({re.sub(r"\D", "", item) for case in _CASES for item in case[:2]})
        script = function + f"console.log(JSON.stringify({json.dumps(numbers)}.map(phoneVariants)))"
        output = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True
        ).stdout
        for number, variants in zip(numbers, json.loads(output)):
            self.assertEqual(set(variants), phone_variants(number), number)


class ContactMatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = open_database(Path(self.tmp.name))
        self.bruno = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
        ).lastrowid

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _contact(self, client, value: str) -> int:
        return self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
            (client, value),
        ).lastrowid

    def test_old_whatsapp_form_finds_the_registered_client(self) -> None:
        contact = self._contact(self.bruno, "+5511987654321")
        entry = open_entry(
            self.db,
            kind="whatsapp",
            value="+551187654321",
            message_id="w1",
            received_at="2026-11-20T09:00:00-03:00",
            text="Bom dia, doutor",
            has_text=True,
            has_file=False,
            account="whatsapp",
        )
        row = self.db.execute(
            "SELECT contato_id, estado FROM entrada WHERE id = ?", (entry,)
        ).fetchone()
        self.assertEqual((row["contato_id"], row["estado"]), (contact, "nova"))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM contato").fetchone()[0], 1)

    def test_registered_old_form_finds_new_form(self) -> None:
        contact = self._contact(self.bruno, "+551187654321")
        self.assertEqual(contact_for(self.db, "whatsapp", "+5511987654321"), (contact, True))

    def test_exact_number_wins_over_equivalent(self) -> None:
        self._contact(None, "+551187654321")
        exact = self._contact(self.bruno, "+5511987654321")
        self.assertEqual(contact_for(self.db, "whatsapp", "+5511987654321"), (exact, True))

    def test_fixed_line_does_not_match_mobile(self) -> None:
        self._contact(self.bruno, "+5511932345678")
        contact, known = contact_for(self.db, "whatsapp", "+551132345678")
        self.assertFalse(known)
        value = self.db.execute("SELECT valor FROM contato WHERE id = ?", (contact,)).fetchone()
        self.assertEqual(value["valor"], "+551132345678")


class NotOnWhatsAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = open_database(self.data)
        session = whatsapp_session(self.data)
        session.mkdir(parents=True)
        (session / "creds.json").write_text(json.dumps({"registered": True}), encoding="utf-8")
        bruno = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
        ).lastrowid
        phone = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', '+5511987654321')",
            (bruno,),
        ).lastrowid
        entry = self.db.execute(
            """
            INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado)
            VALUES (?, ?, '2026-11-20T07:00:00-03:00', 'oi', 'arquivada')
            """,
            (bruno, phone),
        ).lastrowid
        self.draft = self.db.execute(
            """
            INSERT INTO rascunho (
                entrada_id, texto, estado, texto_aprovado, destino_aprovado,
                canal_aprovado, cliente_entrada_aprovado, cliente_contato_aprovado
            ) VALUES (?, 'Bruno, ok.', 'aprovado', 'Bruno, ok.', '+5511987654321',
                      'whatsapp', ?, ?)
            """,
            (entry, bruno, bruno),
        ).lastrowid
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_number_not_on_whatsapp_releases_the_draft_with_notice(self) -> None:
        asked: list[dict] = []

        def runner(_session, op, payload):
            asked.append({"op": op, **payload})
            return {"sent": False, "handed": False, "error": "not_on_whatsapp"}

        self.assertFalse(WhatsAppSender(self.db, self.data, runner).send(self.draft))
        self.assertEqual(asked, [{"op": "send", "to": "+5511987654321", "text": "Bruno, ok."}])
        state = self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?", (self.draft,)
        ).fetchone()["estado"]
        self.assertEqual(state, "aprovado")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertIn("não está no WhatsApp", read_notice(self.data))

    def test_other_refusal_releases_without_that_notice(self) -> None:
        runner = lambda *_args: {"sent": False, "handed": False}  # noqa: E731
        self.assertFalse(WhatsAppSender(self.db, self.data, runner).send(self.draft))
        self.assertEqual(read_notice(self.data), "")


if __name__ == "__main__":
    unittest.main()
