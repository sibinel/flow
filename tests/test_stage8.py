"""Etapa 8: a chamada leva só o pedaço da tarefa. Falha de rede não envia."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path

from flow.controller import Controller
from flow.llm import CHAT_URL, MODEL, CloudLLM, post_chat
from flow.paths import package_dir
from flow.store import open_database


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


def _process(connection, client_id: int, number: str, title: str) -> int:
    return connection.execute(
        'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
        (client_id, number, title),
    ).lastrowid


def _entry(connection, contact_id: int, text: str, client_id: int | None) -> int:
    return connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (?, ?, NULL, '2026-11-23T09:00:00', ?, 'nova')
        """,
        (client_id, contact_id, text),
    ).lastrowid


class ModelCallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)
        self.seen: list[tuple[str, dict, dict]] = []

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def _transport(self, url: str, body: dict, headers: dict) -> dict:
        self.seen.append((url, body, headers))
        return {"choices": [{"message": {"content": "texto pronto"}}]}

    def test_call_carries_the_task_slice_and_not_the_whole_client(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        phone = _contact(self.db, bruno, "whatsapp", "+5511987654321")
        _contact(self.db, bruno, "email", "bruno.dias@email.com")
        _process(self.db, bruno, "1001234-55.2024.8.26.0100", "Rescisão")
        _process(self.db, bruno, "2000", "Inventário")
        ana = _client(self.db, "Ana Lima")
        ana_phone = _contact(self.db, ana, "whatsapp", "+5511900007777")
        _process(self.db, ana, "7777", "Segredo Ana")
        current = _entry(
            self.db,
            phone,
            "Bruno, segue a petição da rescisão.",
            None,
        )
        history = _entry(self.db, phone, "HISTORICO-ENTRADA mensagem antiga", bruno)
        self.db.execute(
            """
            INSERT INTO documento (entrada_id, caminho, nome)
            VALUES (?, 'CAMINHO-SECRETO/segredo.pdf', 'segredo-documento-bruno.pdf')
            """,
            (history,),
        )
        self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'RASCUNHO-VELHO-NAO-VAI', 'aguardando')
            """,
            (history,),
        )
        self.db.execute(
            """
            INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
            VALUES (?, ?, 'whatsapp', '2026-11-01T09:00:00', ?)
            """,
            (bruno, history, "HISTORICO-FORA conversa antiga sobre férias"),
        )
        self.db.execute(
            """
            INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
            VALUES (?, ?, 'whatsapp', '2026-11-02T09:00:00', ?)
            """,
            (bruno, history, "Conversamos sobre a rescisão do contrato"),
        )
        ana_entry = _entry(self.db, ana_phone, "SEGREDO-ANA não entra", ana)
        self.db.execute(
            """
            INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
            VALUES (?, ?, 'whatsapp', '2026-11-02T10:00:00', ?)
            """,
            (ana, ana_entry, "SEGREDO-ANA rescisão confidencial"),
        )
        self.db.commit()

        llm = CloudLLM(self.db, transport=self._transport, api_key="teste")
        self.assertEqual(llm.tool_names(), ())
        self.assertFalse(hasattr(llm, "send"))
        self.assertEqual(
            llm.complete("classificacao", entry_id=current),
            "texto pronto",
        )
        llm.complete(
            "rascunho",
            client_id=bruno,
            message="pode mandar o retorno?",
            query="rescisão",
        )

        self.assertEqual([item[0] for item in self.seen], [CHAT_URL, CHAT_URL])
        classification = json.loads(self.seen[0][1]["messages"][1]["content"])
        draft = json.loads(self.seen[1][1]["messages"][1]["content"])
        raw = json.dumps([item[1] for item in self.seen], ensure_ascii=False)

        self.assertEqual(self.seen[0][1]["model"], MODEL)
        self.assertNotIn("tools", self.seen[0][1])
        self.assertEqual(self.seen[0][2]["Authorization"], "Bearer teste")
        self.assertEqual(
            set(classification),
            {"tarefa", "mensagem", "nome", "contatos", "processos"},
        )
        self.assertEqual(classification["mensagem"], "Bruno, segue a petição da rescisão.")
        self.assertEqual(classification["nome"], "Bruno Dias")
        self.assertEqual(
            classification["contatos"],
            [
                {"tipo": "whatsapp", "valor": "+5511987654321"},
                {"tipo": "email", "valor": "bruno.dias@email.com"},
            ],
        )
        self.assertEqual(
            classification["processos"],
            [
                {"número": "1001234-55.2024.8.26.0100", "título": "Rescisão"},
                {"número": "2000", "título": "Inventário"},
            ],
        )
        self.assertEqual(set(draft), {"tarefa", "mensagem", "trechos"})
        self.assertEqual(draft["tarefa"], "rascunho")
        self.assertEqual(draft["mensagem"], "pode mandar o retorno?")
        self.assertEqual(draft["trechos"], ["Conversamos sobre a rescisão do contrato"])
        for secret in (
            "SEGREDO-ANA",
            "HISTORICO-ENTRADA",
            "HISTORICO-FORA",
            "CAMINHO-SECRETO",
            "segredo-documento-bruno",
            "RASCUNHO-VELHO-NAO-VAI",
            "Ana Lima",
            "+5511900007777",
            "7777",
            "Segredo Ana",
        ):
            self.assertNotIn(secret, raw)
        self.assertNotIn("bruno.dias@email.com", json.dumps(draft, ensure_ascii=False))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

    def test_network_failure_does_not_send(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        phone = _contact(self.db, bruno, "whatsapp", "+5511987654321")
        entry = _entry(self.db, phone, "Bruno, segue a petição da rescisão.", None)
        draft = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Bruno, posso enviar a petição?', 'aprovado')
            """,
            (entry,),
        ).lastrowid
        self.db.commit()

        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()

        def fail(url: str, body: dict, headers: dict) -> dict:
            self.assertEqual(url, CHAT_URL)
            self.assertIn("petição", json.dumps(body, ensure_ascii=False))
            return post_chat(f"http://127.0.0.1:{port}/v1/chat/completions", body, headers)

        llm = CloudLLM(self.db, transport=fail, api_key="teste")
        with self.assertRaises(urllib.error.URLError):
            llm.complete("classificacao", entry_id=entry)

        def tool_call(url: str, body: dict, headers: dict) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"function": {"name": "envio"}}],
                        }
                    }
                ]
            }

        with self.assertRaises(RuntimeError):
            CloudLLM(self.db, transport=tool_call, api_key="teste").complete(
                "classificacao",
                entry_id=entry,
            )

        state = self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?",
            (draft,),
        ).fetchone()["estado"]
        self.assertEqual(state, "aprovado")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        source = (package_dir() / "llm.py").read_text(encoding="utf-8").lower()
        for banned in ("smtplib", "flow.send", "sendtool", "whatsapp"):
            self.assertNotIn(banned, source)


class WindowDoesNotCallTheModelTest(unittest.TestCase):
    def test_window_does_not_call_the_model(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("openai", "complete_model", "api.openai"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
