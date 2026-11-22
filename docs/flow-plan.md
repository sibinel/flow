# flow: plano

O plano ordena a construção. Cada etapa só começa quando o teste da anterior passa.

## 1. Processo e pacote

Constrói um processo Python único, com janela QML vazia, controller e pacote PyInstaller. Os dados ficam fora do pacote.
Artefato: `.app` em `.dmg` no macOS e `.exe` com instalador no Windows, mais a pasta do usuário.
Teste: a janela abre, o advogado não instala Python, e atualizar o pacote não apaga a pasta do usuário.

## 2. SQLite

Constrói o SQLite com o esquema já descrito, inclusive FTS5 em `trecho`. O arquivo fica na pasta do usuário.
Artefato: o banco, com cliente, contato, processo, entrada, documento, pendencia, rascunho, envio e trecho.
Teste: as tabelas existem, `trecho` responde a uma busca FTS5, e o arquivo está fora do pacote.

## 3. Fila

Constrói a fila na janela: aprovar rascunho e indicar pendência. A janela só fala com o controller.
Artefato: a tela da fila, ligada só ao controller.
Teste: aprovar marca o rascunho como aprovado, indicar pendência grava a pendência, e a janela não chama o modelo.

## 4. Classificador

Constrói o classificador, um cliente por vez, com as regras de pendência já escritas. Sem LLM: regras fixas para nome diferente, contato novo e processo não citado.
Artefato: o classificador no harness, só com a tool de leitura.
Teste: esses três casos ficam pendentes, `cliente_id` fica nulo, `contato_id` só aponta o canal, e o classificador não escolhe o processo.

## 5. Leitor de e-mail

Constrói o leitor de e-mail por IMAP. Só leitura. Grava entrada e documento.
Artefato: o leitor IMAP, na tool de leitura.
Teste: uma mensagem com anexo vira entrada e documento no disco, e nada é enviado.

## 6. Remetente

Constrói o remetente SMTP. A tool de envio só existe nele e só depois da aprovação.
Artefato: o remetente SMTP, com a tool de envio registrada só nesse passo.
Teste: rascunho sem aprovação não sai, o classificador não enxerga a tool de envio, e o envio aprovado fica registrado.

## 7. WhatsApp

Constrói o WhatsApp por Baileys, na conta pessoal, sem a API da Meta. QR uma vez. Sessão no disco, fora da pasta sincronizada. Leitura e envio nas mesmas duas tools.
Artefato: a sessão Web no disco, fora do pacote e fora da pasta sincronizada.
Teste: sem a sessão, leitura e envio param até um QR novo, e a sessão não entra na pasta de documentos nem no pacote.

## 8. LLM

Constrói o LLM na nuvem, no padrão OpenAI, com o alcance já escrito. O texto do cliente sai da máquina só nessa chamada.
Artefato: a chamada com o pedaço da tarefa do cliente da vez.
Teste: a classificação vê só a mensagem, o nome, os contatos e a lista de processos; o rascunho e a busca veem só os trechos daquele cliente e a mensagem atual.

## 9. Busca

Constrói a busca "quando falei com o João sobre rescisão" em cima do FTS5. A resposta usa só trechos daquele cliente.
Artefato: a consulta em `trecho` e o rascunho redigido em cima dela.
Teste: a frase acha o cliente e os trechos dele, e a resposta não traz trecho de outro cliente.

## 10. Ciclo do dia

Constrói o ciclo do dia, parametrizado, com padrão de manhã. Nada envia entre ciclos.
Artefato: a configuração de quantidade e horários.
Teste: o ciclo monta follow-up para aprovar e documento para arquivar, e o que chega depois espera o próximo ciclo.

## Fora deste plano

Sync por OneDrive, Google Drive ou equivalente. Backup físico. Escolha do modelo OpenAI. Cofre da senha e do token.

Updates e deletes do SQLite que mexem em mais de uma tabela precisam de uma transação só, em todo ponto que gravar assim. Hoje cada chamada faz o próprio commit. Resolver depois.
