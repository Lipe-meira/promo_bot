# Agente local: iniciar e parar um processo no PC pelo painel

Primeira etapa do controle do bot pelo painel. O que se liga e desliga hoje é um **processo de teste que
não faz nada** (`promo_agent.harmless_process`: só espera a entrada fechar). O bot real não está ligado a
estes botões.

## Como funciona

```
Painel (navegador) ──Clerk──▶ API ◀──token do agente── promo-agent (seu PC, Windows) ──▶ 1 processo
```

- O agente **só faz conexões de saída** para a API (HTTPS; `http` apenas para `localhost`). Nada se conecta
  ao PC e nenhuma porta é aberta.
- A cada 3 s o agente informa o estado do processo (`running` ou `stopped`) e recebe no máximo um comando.
- Os únicos comandos são `start` e `stop`. O programa executado é fixo no código do agente: o servidor não
  envia caminho, argumentos nem configuração.
- Sem notícia do agente por 12 s, o painel mostra **Agente desconectado** e o processo como
  **desconhecido**. Um relatório antigo nunca é apresentado como estado atual.
- Com o agente desconectado a API recusa comandos (409): nada fica na fila para rodar mais tarde. Um
  comando não concluído em 30 s é descartado.

## Pareamento

1. No painel (**Dashboard → Agente local**), gere o código. Ele vale 10 minutos, uma vez, e um código
   novo anula o anterior.
2. No PC, na pasta `apps\agent`, execute o comando mostrado na tela e digite o código quando for pedido:

```powershell
uv run promo-agent pair --api https://<endereço-da-api>
```

3. Deixe o agente em execução (Ctrl+C encerra e para o processo):

```powershell
uv run promo-agent run
```

Há **um agente por workspace**: parear de novo substitui o anterior, cujo token deixa de valer. **Remover
agente** no painel revoga o token; o agente para o processo e encerra. `promo-agent status` mostra o
pareamento deste PC e `promo-agent unpair` o apaga.

## Por que não surgem dois processos

| Situação | Barreira |
| --- | --- |
| Cliques repetidos | a API guarda um único comando pendente por workspace, com a linha travada; pedir o que já foi pedido, ou o que já é o estado, não cria comando |
| Comando entregue duas vezes | o agente não executa o mesmo número de comando outra vez |
| `start` com o processo vivo | o supervisor não cria outro |
| Dois agentes no mesmo PC | trava de arquivo: o segundo se recusa a rodar |
| Agente morto à força | o processo está num Job Object do Windows com `KILL_ON_JOB_CLOSE`: morre junto, com os filhos |
| Token copiado para outro PC | o arquivo é cifrado com DPAPI para o usuário do Windows que pareou |

Um processo que termina sozinho **não é reiniciado**; o painel avisa.

## Credenciais

- Telegram, AliExpress e demais credenciais do bot **ficam no PC**. O protocolo não tem campo para elas e a
  API recusa campos extras no relatório do agente.
- O único segredo novo é o token do agente: no PC, cifrado em `%LOCALAPPDATA%\PromoBotAgent\pairing.json`;
  no servidor, apenas o hash SHA-256. Do código de pareamento o servidor também guarda só o hash.
- O agente não segue redirecionamentos nem usa proxy do ambiente, para o token não ir a outro endereço.

## Implantação (não executada; exige autorização própria)

A migration `0003` cria `agents` e `agent_pairing_codes`. Como o `/readyz` compara a revisão do banco com a
do código, a ordem é: aplicar a `0003` no Neon (ver
[SAAS_BACKEND_RENDER_NEON](SAAS_BACKEND_RENDER_NEON.md)), implantar a API, implantar a web e então parear.

## Limites

- Só Windows, em primeiro plano: não é serviço, não inicia com o sistema. Fechar a janela encerra tudo.
- O painel pode levar até cerca de 12 s para perceber que um agente morreu sem se despedir.
- Enquanto o agente roda (ou o Dashboard está visível), há chamadas a cada 3 s: a API e o banco **não
  entram em repouso**. Confira a cota dos planos gratuitos do Render e do Neon antes de deixá-lo ligado
  continuamente.
- Sem contato com a API, o processo continua como estava; o painel não consegue pará-lo até a volta.
- `/v1/agent/pair` e `/v1/agent/sync` não têm limite de taxa. Código (~59 bits) e token (256 bits) não
  são adivinháveis, mas tentativas chegam ao banco.

## Antes de ligar o bot real

O pedido de parada atual (fechar a entrada do processo) só é entendido pelo processo de teste; o bot seria
encerrado à força após 5 s. É preciso, antes: parada ordenada que respeite a persistência antes do envio e
as entregas ambíguas (por exemplo, `CTRL_BREAK` tratado no bot), escolha explícita da pasta, configuração e
modo (shadow) no PC, e um estado que diferencie "processo vivo" de "bot conectado".
