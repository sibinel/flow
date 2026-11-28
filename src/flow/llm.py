"""LLM na nuvem, no padrão OpenAI.

O texto do cliente sai da máquina só nesta chamada, e só o pedaço da tarefa.
A classificação leva a mensagem, o nome, os contatos e a lista de processos.
O rascunho e a busca levam os trechos daquele cliente e a mensagem atual.
A chamada não envia, não cria cliente e não executa tool.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

from flow.store import search_trechos_do_cliente, search_trechos_relevantes

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"

_SYSTEM = {
    "classificacao": (
        "Você vê só a mensagem, o nome, os contatos e a lista de processos. "
        "Não escolha um processo que o texto não cita. Não crie cliente. Não envie."
    ),
    "rascunho": (
        "Redija só com os trechos deste cliente e a mensagem atual. "
        "Não use outro cliente. Não envie."
    ),
    "busca": (
        "Responda só com os trechos deste cliente e a mensagem atual. "
        "Não use outro cliente. Não envie."
    ),
}


def load_model_key(data_dir: Path) -> str:
    """Chave no arquivo local. Sem o arquivo, a chamada não sai."""
    path = Path(data_dir) / "modelo.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    key = data.get("api_key")
    if not isinstance(key, str):
        return ""
    return key.strip()


class CloudLLM:
    def __init__(self, connection: sqlite3.Connection, transport=None, api_key: str = "") -> None:
        self._db = connection
        self._transport = transport or post_chat
        self._api_key = api_key

    def tool_names(self) -> tuple[str, ...]:
        return ()

    def complete(
        self,
        task: str,
        *,
        entry_id: int = 0,
        client_id: int = 0,
        message: str = "",
        query: str = "",
    ) -> str:
        if task not in _SYSTEM:
            raise ValueError("tarefa desconhecida")
        if not self._api_key:
            raise RuntimeError("chave do modelo ausente")
        piece = _piece(self._db, task, entry_id, client_id, message, query)
        body = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM[task]},
                {"role": "user", "content": json.dumps(piece, ensure_ascii=False)},
            ],
        }
        response = self._transport(CHAT_URL, body, _headers(self._api_key))
        return _text(response)


def post_chat(url: str, body: dict, headers: dict) -> dict:
    """POST no padrão chat completions. Falha de rede sobe; não envia mensagem."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        raise


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _piece(
    connection: sqlite3.Connection,
    task: str,
    entry_id: int,
    client_id: int,
    message: str,
    query: str,
) -> dict:
    if task == "classificacao":
        return _classification(connection, entry_id)
    return _draft(connection, task, client_id, message, query)


def _classification(connection: sqlite3.Connection, entry_id: int) -> dict:
    entry = connection.execute(
        "SELECT texto, contato_id FROM entrada WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if entry is None:
        raise ValueError("entrada não existe")
    contact = connection.execute(
        "SELECT cliente_id FROM contato WHERE id = ?",
        (entry["contato_id"],),
    ).fetchone()
    if contact is None:
        raise ValueError("contato não existe")
    client = connection.execute(
        "SELECT nome FROM cliente WHERE id = ?",
        (contact["cliente_id"],),
    ).fetchone()
    if client is None:
        raise ValueError("cliente não existe")
    owner = contact["cliente_id"]
    contacts = [
        {"tipo": row["tipo"], "valor": row["valor"]}
        for row in connection.execute(
            "SELECT tipo, valor FROM contato WHERE cliente_id = ? ORDER BY id",
            (owner,),
        )
    ]
    processes = [
        {"número": row["número"], "título": row["título"]}
        for row in connection.execute(
            'SELECT "número", "título" FROM processo WHERE cliente_id = ? ORDER BY id',
            (owner,),
        )
    ]
    return {
        "tarefa": "classificacao",
        "mensagem": entry["texto"],
        "nome": client["nome"],
        "contatos": contacts,
        "processos": processes,
    }


def _draft(
    connection: sqlite3.Connection,
    task: str,
    client_id: int,
    message: str,
    query: str,
) -> dict:
    if task == "rascunho":
        # O rascunho busca por qualquer palavra; a busca por tema exige todas.
        rows = search_trechos_relevantes(connection, client_id, query)
    else:
        rows = search_trechos_do_cliente(connection, client_id, query)
    current = (message or "").strip()
    return {
        "tarefa": task,
        "mensagem": message,
        # A mensagem atual já vai em "mensagem"; o trecho dela não se repete.
        "trechos": [row["texto"] for row in rows if row["texto"].strip() != current],
    }


def _text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("resposta vazia do modelo")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise RuntimeError("o modelo não devolveu texto")
    return content
