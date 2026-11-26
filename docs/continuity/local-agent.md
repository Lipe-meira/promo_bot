# Checkpoint: agente local (TASK 02, 25/11/2026)

**Objetivo:** iniciar e parar, pelo painel, um processo de teste inofensivo no PC do dono e ver o estado
real. Desenho, uso e limites em [LOCAL_AGENT](../LOCAL_AGENT.md).

**Branch:** `codex/panel-local-agent` (worktree `F:\projetos\promo_bot_agent`), criada de `origin/main` em
`ed09e2f54f1099cf97a186df77355b90a6d013ef` (merge do PR #15).
[PR #16](https://github.com/Lipe-meira/promo_bot/pull/16), Draft: **sem merge, sem deploy, sem migration em
banco real.** Último commit de código publicado e confirmado no remoto antes deste registro:
`7f50e82` (a branch foi publicada em `1b92a8832f182dc9fea7085602ef69bdaf7ac6ec`). O CI do PR ainda não tinha
terminado quando este registro foi salvo. O SHA do commit que contém este arquivo vem do Git
(`git log -1 --format=%H -- docs/continuity/local-agent.md`).

**Atenção para quem retomar:** existe `origin/codex/panel-pilot-rollout` (não mergeada) com o registro, em
`saas-backend.md`, da implantação do #15 e da confirmação do cadastro de canais pelo operador. Esta tarefa
não tocou aquele arquivo, para não conflitar.

## O que mudou

- `apps/api`: migration `0003` (`agents`, `agent_pairing_codes`), `local_agent.py`, 6 rotas, capability
  `local_agent_test`, `openapi.json`.
- `apps/agent` (novo): `promo-agent pair|run|status|unpair`, sem dependências de runtime.
- `apps/web`: cartão **Agente local (teste)** no Dashboard, cliente tipado, mensagens (incluindo
  `DATABASE_UNAVAILABLE`, que antes caía no texto genérico).
- CI: job `agent` em `windows-latest` no workflow `saas-web`.
- `src/promo_bot` não foi alterado.

## Validação (dados sintéticos; sem Telegram, AliExpress ou serviços reais)

| Verificação | Resultado |
| --- | --- |
| `apps/api`: lock, `ruff`, `mypy` strict, `openapi --check` | limpos |
| `apps/api`: `pytest` sem banco | 252 passed |
| `apps/api`: `pytest -m postgres` (PostgreSQL descartável, migrations reais) | 127 passed |
| `apps/agent`: lock, `ruff`, `mypy` strict, `pytest` (processos reais no Windows, API falsa em loopback) | 38 passed |
| `apps/web`: `check:api`, `typecheck`, `vitest`, `next build` com valores sintéticos | limpos; 161 passed |
| Raiz: `uv lock --check --offline`, `ruff format --check`, `ruff check` | limpos |

**Ponta a ponta neste PC** (roteiro temporário, não versionado): API real em `127.0.0.1` com PostgreSQL
descartável e identidade sintética (chave efêmera), agente real e processo de teste real.

- Pareamento por código; 8 `start` concorrentes geraram **um** processo (no venv do Windows são 2 PIDs:
  lançador e interpretador); outro `start` não criou nada; `stop` deixou zero.
- Agente morto à força: o processo morreu junto; o painel passou a desconectado/desconhecido e `start` deu
  409 `AGENT_OFFLINE`. Ao voltar: parado, sem comando pendente.
- Remoção no painel: o agente parou o processo e encerrou.
- O cartão do painel (em jsdom, com `fetch` real contra essa API) percorreu o mesmo fluxo com o agente e o
  processo reais, incluindo duplo clique em iniciar.

**Mutações** (cada barreira desligada faz um teste falhar): idempotência na API, recusa com agente offline,
estado antigo exibido, conclusão antiga apagando comando novo, `KILL_ON_JOB_CLOSE`, `start` repetido no
supervisor, reexecução de comando no agente, ordenação das leituras na tela e estado mantido após falha de
leitura. A mutação da ordenação mostrou que o primeiro teste da tela segurava a chamada errada (`/v1/me`);
o teste foi corrigido antes do commit. Quatro processos que a mutação do Job Object deixou vivos foram
encerrados.

**Não executado:** navegador real com Clerk (o fluxo autenticado só será verificável após o deploy ou com a
web local usando as chaves de desenvolvimento do dono); API pela internet; suíte raiz do bot (nada do bot
mudou; roda no CI `bot-root`); teste de longa duração.

**TDD:** testes escritos antes da implementação na API (22 falhando), no agente e no cartão (falha por
módulo ausente). As migrations, o CSS e o CI não têm teste próprio anterior.

## Segunda passada (25/11/2026): duas falhas de "estado real"

Revisão do código por outra sessão, com a branch em `25fb8fa0be16a1da3ec2c1bfcea496084b291949`. Antes de
alterar, repetidos localmente: agente 38, API sem banco 252, web 161.

- **Painel:** leitura ou comando sem resposta deixava o último estado na tela; um comando travado também
  suspendia as leituras. Agora há limite de 10 s, o estado vira desconhecido e as leituras continuam.
- **Agente:** resposta não HTTP ou cortada (`BadStatusLine`, `IncompleteRead`) escapava do cliente e
  derrubaria o agente. Agora é tratada como falta de contato.

Os 4 testes novos falharam antes da correção. Depois dela: agente `lock`, `ruff`, `mypy` e **40 passed**
(processos reais); web `vitest` **163 passed**, `tsc`, `check:api` e `next build` sintético limpos.
Não repetidos localmente: testes da API com PostgreSQL (a API não mudou; rodam no CI) e o ponta a ponta com
banco descartável. Limite novo registrado em [LOCAL_AGENT](../LOCAL_AGENT.md#antes-de-ligar-o-bot-real):
um PC substituído e sem rede mantém o processo dele rodando.

## Correção do backoff (25/11/2026)

Falha reproduzida pelo dono, com a branch em `b57667d50c37a6009809cc4ded739d57f88d5f27`: o atraso era calculado
como `3 * 2**falhas`, e depois de 1.024 falhas seguidas (cerca de 4h15 de espera acumulada) a conversão estourava
(`OverflowError`), encerrando o agente e o processo. Agora o próprio atraso dobra e para no teto de 15 s, sem
contador. Teste novo com espera simulada (5.000 falhas, volta do contato e segunda queda) falhou antes com o mesmo
`OverflowError` e passa depois. Agente: lock, `ruff`, `mypy` e **41 passed**. API e web não mudaram e não foram
repetidas localmente. Não houve teste de queda real por horas.

## Limite de chamadas nas rotas do agente (25/11/2026)

**Branch** `codex/agent-rate-limit` (worktree `F:\projetos\promo_bot_ratelimit`), criada de `origin/main` em
`bd3a7d0964fbf0c0d89bd00a970bf1c4b6c8ec3c` (merge do PR #16). [PR #17](https://github.com/Lipe-meira/promo_bot/pull/17),
Draft, publicado até `0a4ac8172fba46b66dbd2402a3933a8ca11c8957` antes desta linha: **sem merge, sem deploy, sem migration em
banco real**; esta mudança não cria migration. Regras e limites em
[LOCAL_AGENT](../LOCAL_AGENT.md#limite-de-chamadas-do-agente).

- API: `ratelimit.py` (baldes em memória), aplicado como dependência em `/v1/agent/pair` e `/v1/agent/sync`;
  429 `RATE_LIMITED` com `Retry-After`; ajuste opcional `PROMO_API_TRUSTED_PROXY_HOPS`; contrato regenerado.
- Agente: trata 429 como espera (não como falta de contato nem remoção do pareamento), no `run` e no `pair`.

| Verificação | Resultado |
| --- | --- |
| API: `ruff`, `mypy`, `openapi --check`, `pytest` sem banco | limpos; 266 passed |
| API: `pytest -m postgres` (PostgreSQL descartável) | 133 passed |
| Agente: lock, `ruff`, `mypy`, `pytest` | limpos; 56 passed |
| Web: `check:api`, `typecheck`, `vitest` (só o `schema.d.ts` mudou) | limpos; 163 passed |

Com relógio real, API em `127.0.0.1` e o agente de verdade: 75 s de uso normal com 18 comandos sem nenhum 429;
100 chamadas com tokens desconhecidos deram 10x401 e 90x429 e o agente seguiu conectado; um excesso com o token do
próprio agente fez o agente anunciar a espera, e depois ele executou `stop` com o mesmo pareamento; o pareamento
limitado devolveu 429 e o mesmo código funcionou após o `Retry-After`. Mutações (agente nunca reconhecido, token
removido que continua reconhecido, pareamento sem limite) fazem testes falharem.

**Não verificado:** o limite atrás do proxy do Render (endereço visto pela API), várias instâncias e carga real.
**Implantação:** não exige migration; basta implantar a API e atualizar o agente no PC. Um agente antigo trata
o 429 como falta de contato e tenta de novo em até 15 s, sem respeitar o `Retry-After`.

## Proxy no Render: `--no-proxy-headers` (25/11 e 26/11/2026)

**Preflight somente leitura do piloto (25/11):** Neon com `alembic_version` `0002`; Render e Vercel (Production)
em `ed09e2f54f1099cf97a186df77355b90a6d013ef`; Vercel sem repositório Git conectado; Blueprint do Render com **Auto
Sync ligado**; `PROMO_API_TRUSTED_PROXY_HOPS` não definida. Um `GET /healthz` com `X-Forwarded-For` forjado apareceu no
log do Render com o endereço forjado: o Uvicorn confiava no cabeçalho. A `0003` foi ensaiada (ida, repetição
abortada e volta) só num PostgreSQL local descartável. **Nada foi alterado nos serviços.**

**Correção (26/11):** branch `codex/render-no-proxy-headers` (worktree `F:\projetos\promo_bot_proxy`), de
`origin/main` em `7d1681ad18a13c9d4e0ee86c106f89c6d2d8261f` (merge do PR #17). `startCommand` ganha
`--no-proxy-headers`; testes exigem a opção e o padrão 0 de `PROMO_API_TRUSTED_PROXY_HOPS`; documentação
corrigida. PR Draft: **sem merge, sem push na branch do Blueprint, sem migration e sem deploy.**

Ensaio local do app real (25/11), imitando o Render com `FORWARDED_ALLOW_IPS=*`, oito pareamentos com endereços
forjados: sem a opção, oito 401 e oito endereços no log; com ela, cinco 401, depois 429, e só o endereço real.

**Ordem quando houver autorização:** merge → `0003` no Neon → avançar
`codex/saas-backend-render-neon-prep` até a `main` (o push que muda o `render.yaml` **já é o deploy** da API) →
conferir comando, `/healthz`, `/readyz`, rota do agente, log e 429 → publicar o painel → testar no Windows.
**Não verificado:** o comportamento no Render com a opção, e se a sincronização do Blueprint implanta o commit
novo (se não, deploy manual).

## Pendências

1. Revisão do PR; merge e deploy só com autorização específica.
2. Implantar na ordem da seção sobre o proxy, acima, depois parear no PC e confirmar pelo painel.
3. Decidir sobre a cota dos planos gratuitos antes de manter o agente ligado (consultas a cada 3 s).
4. Antes do bot real: parada ordenada (ver [LOCAL_AGENT](../LOCAL_AGENT.md#antes-de-ligar-o-bot-real)).
