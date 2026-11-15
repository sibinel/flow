# flow: implementação

Discussão da arquitetura de implementação. Nada aqui está fechado.

O aplicativo roda na máquina do advogado. O disco guarda documentos e um SQLite. A nuvem só sincroniza.

## Pontos em aberto

- **Linguagem:** em qual linguagem o app flow é escrito.
- **Framework:** o que organiza leitores, classificador, fila e remetente no mesmo processo.
- **Interface visual:** onde o advogado vê a fila, aprova o rascunho e aponta pendência.
- **LLM:** onde o modelo entra, o que ele vê de cada cliente, e o que ele não pode fazer.
- **SQLite:** como o índice e o estado do fluxo ficam no mesmo arquivo, na pasta local.
- **Leitores e remetente:** como o app fala com WhatsApp e e-mail sem misturar leitura e envio.

## Já decidido

- Leitores só entram. Remetente só sai, e só depois da aprovação.
- Classificador vê um cliente por vez.
- Nome diferente, contato novo ou processo não citado ficam pendentes.
- Documento fica no disco. SQLite indexa e guarda o estado. Os quatro componentes usam o banco, sem seta de fluxo até ele.
