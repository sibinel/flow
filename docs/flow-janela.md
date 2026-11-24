# Ligação da janela

A janela continua falando só com o controller. Não chama leitor, remetente nem modelo.

## O que a tela ganha

- Um timer no processo, a partir da abertura, olha `ciclo.json`. No horário, chama `run_cycle`. Fora do horário, nada sai.
- O ciclo, nesse horário, coleta e-mail e WhatsApp antes de montar a fila. A coleta não roda o dia inteiro.
- Um campo de busca chama `search` e a resposta aparece como rascunho na fila, para aprovar.
- Um botão de pareamento chama `pair_whatsapp` e mostra o QR. Sem sessão, a tela diz que leitura e envio de WhatsApp estão parados.

## O que continua fora

Aprovar e indicar permanecem como estão. O envio continua só no ciclo, depois da aprovação.

Host, porta, usuário e senha ainda não existem em disco. Sem o cofre, o ciclo não tem conta para coletar e-mail. O pareamento do WhatsApp não depende disso.

## Teste

Abrir a janela não envia. No horário configurado, o ciclo coleta e monta a fila. Fora do horário, nada sai. A busca devolve rascunho só do cliente da vez. Sem sessão, o pareamento pede QR e a coleta de WhatsApp não corre.
