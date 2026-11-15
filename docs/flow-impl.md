# flow: implementação

Discussão da arquitetura de implementação. O que não está em "Já decidido" continua aberto.

O aplicativo roda na máquina do advogado. O disco guarda documentos e um SQLite. A nuvem só sincroniza.

## Já decidido

Um processo Python, em Windows e macOS.

- **QML / Qt:** a janela. Mostra a fila, recebe a aprovação e a indicação de pendência. Não chama o modelo.
- **Controller:** a fronteira entre a janela e o resto. A janela só fala com ele.
- **Agent harness:** LangGraph, com tools, estado e sessão. Leitores, classificador, fila, remetente, SQLite e o LLM ficam aqui.

Leitores só entram. Remetente só sai, e só depois da aprovação. Classificador vê um cliente por vez. Nome diferente, contato novo ou processo não citado ficam pendentes. Documento fica no disco. SQLite indexa e guarda o estado. Os quatro componentes usam o banco, sem seta de fluxo até ele.

## Pontos em aberto

- **LLM:** o que o modelo vê de cada cliente, e o que ele não pode fazer.
- **SQLite:** como o índice e o estado do fluxo ficam no mesmo arquivo, na pasta local.
- **Leitores e remetente:** como o harness fala com WhatsApp e e-mail sem misturar leitura e envio.
