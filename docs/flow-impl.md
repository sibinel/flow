# flow: implementação

Discussão da arquitetura de implementação. O que não está em "Já decidido" continua aberto.

O aplicativo roda na máquina do advogado. O disco guarda documentos e um SQLite. A nuvem só sincroniza.

## Já decidido

Dois processos, um só aplicativo que o advogado abre.

- **Frontend:** a janela, em Windows e macOS. Mostra a fila, recebe a aprovação e a indicação de pendência. Não chama o modelo.
- **Runtime:** o app flow, em Python. Leitores, classificador, fila, remetente, SQLite e o LLM ficam aqui.

Leitores só entram. Remetente só sai, e só depois da aprovação. Classificador vê um cliente por vez. Nome diferente, contato novo ou processo não citado ficam pendentes. Documento fica no disco. SQLite indexa e guarda o estado. Os quatro componentes usam o banco, sem seta de fluxo até ele.

## Pontos em aberto

- **Framework da janela:** com o que o frontend é feito, e como os dois processos se falam.
- **LLM:** o que o modelo vê de cada cliente, e o que ele não pode fazer.
- **SQLite:** como o índice e o estado do fluxo ficam no mesmo arquivo, na pasta local.
- **Leitores e remetente:** como o runtime fala com WhatsApp e e-mail sem misturar leitura e envio.
