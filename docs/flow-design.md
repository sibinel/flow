# flow: arquitetura inicial

A fonte da verdade fica na máquina do advogado. Cinco peças.

## Leitores

Leem WhatsApp e e-mail e só entregam mensagem e anexo. Não enviam nada.

## Repositório local

Guarda cliente, processo, documento, rascunho e pendência. A nuvem é só a cópia sincronizada.

## Classificador

Roda em cada ciclo. Número ou endereço do remetente acha o cliente. Nome diferente, contato novo ou processo não citado viram pendência. Cada chamada vê só os dados daquele cliente.

## Fila

Mostra follow-up para aprovar e documento para arquivar. Não envia. Quando o advogado aprova, marca o rascunho como aprovado.

## Remetente

Só age quando a fila marca o rascunho como aprovado. Usa a conta de WhatsApp ou de e-mail e registra o envio no repositório. Sem aprovação, esse passo não existe.

O agente não fala direto com WhatsApp nem com e-mail. Ele escreve no repositório. Os leitores só entram. O remetente só sai, e só depois da aprovação.
