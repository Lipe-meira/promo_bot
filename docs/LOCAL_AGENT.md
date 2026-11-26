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
- Se a própria API não responder ao painel em 10 s, ele também passa a **desconhecido** e continua
  tentando. Uma resposta HTTP cortada ou inválida conta para o agente como falta de contato.
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
- O limite das rotas do agente é por processo da API (ver acima). Não substitui proteção de borda contra
  excesso de tráfego.

## Limite de chamadas do agente

`/v1/agent/pair` e `/v1/agent/sync` não usam sessão Clerk e têm limite próprio, conferido antes de qualquer
acesso ao banco. Acima dele a resposta é **429** (`RATE_LIMITED`) com `Retry-After` em segundos.

| Quem chama | Rajada | Depois | Contado por |
| --- | --- | --- | --- |
| Agente com token já aceito pelo banco | 30 | 1 por segundo | token |
| Pareamento | 5 | 1 a cada 12 s | endereço |
| `sync` com token desconhecido | 10 | 1 a cada 6 s | endereço |
| Teto somado dos dois últimos | 60 | 1 por segundo | processo |

- O ritmo normal (a cada 3 s, mais uma chamada após cada comando) fica bem abaixo do limite do agente, e
  chamadas de desconhecidos não consomem o limite de um agente já aceito.
- O agente espera o tempo pedido (nunca menos de 3 s), mantém pareamento, processo e resultado ainda não
  informado, e volta sozinho. Esperas acima de 12 s aparecem no painel como **desconectado**.
- Um pareamento recusado por limite **não consome o código**; o comando avisa quanto esperar.
- A contagem fica **na memória de um processo da API**: reinicia com ele e não seria compartilhada entre várias
  instâncias. Não há migration.
- **No Render**, o tratamento padrão do Uvicorn acredita no `X-Forwarded-For` escrito por quem chama: em
  25/11/2026 um endereço forjado apareceu no log como o do cliente. Assim o limite por endereço seria
  contornável, qualquer que fosse a configuração da API. Por isso o `startCommand` do `render.yaml` usa
  `--no-proxy-headers`: o cabeçalho é ignorado. Quem iniciar a API de outro modo atrás de um proxy precisa da
  mesma opção. Uma versão anterior deste texto afirmava, errado, que bastava o padrão da API.
- Com a opção, o endereço contado é o do proxy, então os desconhecidos **dividem o mesmo limite**: um excesso
  de terceiros pode atrasar um pareamento novo ou o primeiro reconhecimento do agente após a API reiniciar. Um
  agente já reconhecido não é afetado.
- `PROMO_API_TRUSTED_PROXY_HOPS` fica no padrão **0**. Acima disso a própria API passa a ler o
  `X-Forwarded-For`, mesmo com a opção acima. Só mude depois de verificar quantas entradas o Render acrescenta
  (**ainda não verificado**): um valor alto demais volta a permitir endereços inventados.

## Antes de ligar o bot real

O pedido de parada atual (fechar a entrada do processo) só é entendido pelo processo de teste; o bot seria
encerrado à força após 5 s. É preciso, antes: parada ordenada que respeite a persistência antes do envio e
as entregas ambíguas (por exemplo, `CTRL_BREAK` tratado no bot), escolha explícita da pasta, configuração e
modo (shadow) no PC, e um estado que diferencie "processo vivo" de "bot conectado".

Também: um agente substituído só para o processo quando volta a falar com a API. Se o PC antigo estiver sem
rede e outro for pareado e iniciado, haverá **dois processos, um em cada PC**. É inofensivo com o processo
de teste; com o bot real exigiria que o agente parasse sozinho após um tempo sem contato.
