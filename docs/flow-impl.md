# flow: implementação

Discussão da arquitetura de implementação. O que não está em "Já decidido" continua aberto.

O aplicativo roda na máquina do advogado. O disco guarda documentos e um SQLite. A nuvem só sincroniza.

## Já decidido

Um processo Python, em Windows e macOS.

- **QML / Qt:** a janela. Mostra a fila, recebe a aprovação e a indicação de pendência. Não chama o modelo.
- **Controller:** a fronteira entre a janela e o resto. A janela só fala com ele.
- **Agent harness:** LangGraph, com tools, estado e sessão. Leitores, classificador, fila, remetente, SQLite e o LLM ficam aqui.

## Instalável

Um pacote por sistema, com o processo Python inteiro dentro. PyInstaller junta Python, Qt, QML e LangGraph. O advogado não instala Python.

- macOS: um `.app` distribuído em `.dmg`.
- Windows: um `.exe` distribuído por um instalador.

Documentos e o SQLite ficam na pasta do usuário, fora do pacote. Atualizar o aplicativo não apaga esses dados.

## SQLite

O arquivo do documento fica no disco. O banco guarda o estado e o índice.

- **cliente:** id, nome, status (prospecção ou cliente)
- **contato:** id, cliente_id, tipo (whatsapp ou email), valor
- **processo:** id, cliente_id, número, título
- **entrada:** id, cliente_id nulo, contato_id, processo_id nulo, recebido_em, texto, estado (nova, pendente, arquivada)
- **documento:** id, entrada_id, caminho no disco, nome
- **pendencia:** id, entrada_id, motivo (nome diferente, contato novo, processo não citado)
- **rascunho:** id, entrada_id, texto, estado (aguardando, aprovado, enviado)
- **envio:** id, rascunho_id, enviado_em, canal
- **trecho:** id, cliente_id, entrada_id, canal, quando, texto

`cliente_id` fica nulo enquanto a entrada está pendente de classificação. `contato_id` aponta o canal por onde chegou, número ou e-mail, e pode ser de outra pessoa. Não se arquiva pelo dono do canal. `processo_id` só é preenchido quando a mensagem cita o processo. Nome diferente ou contato novo não cria cliente: fica pendência. O rascunho só vira envio depois da aprovação.

A busca por tema usa FTS5 na tabela `trecho`. "Quando falei com o João sobre rescisão" acha o cliente e os trechos. O LLM só redige a resposta em cima desses trechos e não lê outro cliente. Texto extraído de documento entra no mesmo índice.

Leitores só entram. Remetente só sai, e só depois da aprovação. Classificador vê um cliente por vez. Nome diferente, contato novo ou processo não citado ficam pendentes. Documento fica no disco. SQLite indexa e guarda o estado. Os quatro componentes usam o banco, sem seta de fluxo até ele.

Duas tools separam leitura e envio. A tool de leitura fica no classificador e nos leitores. A tool de envio só é registrada no remetente, e só entra no grafo quando a fila marca o rascunho como aprovado. A conta de WhatsApp ou de e-mail pode ser a mesma. Sem essa tool registrada, o modelo não consegue chamar o envio.

## Pontos em aberto

- **LLM:** o que o modelo vê de cada cliente fora da busca, e o que ele não pode fazer.
