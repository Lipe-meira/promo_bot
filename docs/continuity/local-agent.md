# Checkpoint: agente local (TASK 02, 25/11/2026)

**Objetivo:** iniciar e parar, pelo painel, um processo de teste inofensivo no PC do dono e ver o estado
real. Desenho, uso e limites em [LOCAL_AGENT](../LOCAL_AGENT.md).

**Branch:** `codex/panel-local-agent` (worktree `F:\projetos\promo_bot_agent`), criada de `origin/main` em
`ed09e2f54f1099cf97a186df77355b90a6d013ef` (merge do PR #15). PR Draft: **sem merge, sem deploy, sem
migration em banco real.** O SHA do commit que contém este arquivo vem do Git
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

## Pendências

1. Revisão do PR; merge e deploy só com autorização específica.
2. Implantar na ordem `0003` no Neon → API → web, depois parear no PC e confirmar pelo painel.
3. Decidir sobre a cota dos planos gratuitos antes de manter o agente ligado (consultas a cada 3 s).
4. Antes do bot real: parada ordenada (ver [LOCAL_AGENT](../LOCAL_AGENT.md#antes-de-ligar-o-bot-real)).
