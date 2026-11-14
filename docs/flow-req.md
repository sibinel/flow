# flow: requisitos iniciais

Agente administrativo para profissionais liberais. O primeiro público é o advogado solo.

## Quem usa

Advogado com carteira própria, dezenas de clientes ativos e processos em mais de um tribunal. A caixa pessoal se mistura com a do escritório. Ele atende, escreve, cobra e ainda responde e-mail e WhatsApp no mesmo dia, sem secretária.

## O que a primeira versão faz

Follow-up de clientes e organização de documentos. A aposta é que isso cobre cerca de 70% do tempo administrativo.

- Lê e-mail e WhatsApp.
- Separa prazo, pedido e arquivo.
- Guarda o documento no cliente certo, e no processo quando a mensagem o citar.
- Deixa o texto do follow-up pronto.
- Envia só depois que o advogado confirma. Nada sai no meio do ciclo.

## Fonte da verdade

Um repositório único, na máquina do advogado. A nuvem guarda uma cópia sincronizada. E-mail e WhatsApp só alimentam esse repositório. Documento, cliente e rascunho do follow-up ficam lá. Nada vive só na conversa.

## Estrutura

Cliente é a raiz. Processo só existe ligado a um cliente. Cliente em prospecção pode ficar sem processo até virar caso.

Mensagem cai primeiro no cliente. Se citar um processo, o agente liga nela. Se não citar, fica no cliente até o advogado apontar.

## Fila do dia

Cada ciclo monta a mesma fila:

- follow-up para aprovar
- documento para arquivar

O padrão é um ciclo de manhã. A quantidade e os horários são parametrizados. O que chega entre um ciclo e outro espera o próximo.
