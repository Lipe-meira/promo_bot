# Checkpoint — preparação técnica do backend SaaS (17/11/2026)

Estado: **preparado e validado offline; nada provisionado.** Aguarda **auditoria independente**
antes de qualquer provisionamento externo. Não declarar a aplicação pronta para produção.
Contrato, runbook, riscos e checklist de autorizações: [SAAS_BACKEND_RENDER_NEON.md](../SAAS_BACKEND_RENDER_NEON.md);
revisão de segurança do diff: [SAAS_BACKEND_DIFFERENTIAL_REVIEW.md](../SAAS_BACKEND_DIFFERENTIAL_REVIEW.md).

## Autorização e limites

Autorizado e feito: leitura, edição no escopo, testes offline com PostgreSQL **descartável** (Docker
local), simulação em contêiner local, commits, push da branch e PR Draft. **Não autorizado e não
feito:** criar projetos Neon/Render, planos ou cartão, deploy, alterar Vercel/Clerk reais, migrations
em banco externo, convites/usuários reais, Telegram/AliExpress/workers/envios do bot, alterar PR #9
ou PR #10, merge, push na `main`.

## Git e publicação

- Repositório: `Lipe-meira/promo_bot`. Branch: `codex/saas-backend-render-neon-prep`. Worktree:
  `F:\projetos\promo_bot_saas_backend` (reutilizar ao continuar; **não** usar `F:\projetos\promo_bot_saas`,
  que guarda a branch documental `codex/dev-preview-readiness`, nem os worktrees das PR #9 e #10).
- Base: `2803af7c95e1dbc61c6d3e07d0f6ae0b8bc21cbb` (= PR #10 `a98b94224f2bbecce95fae5c1f15d420988cab00`
  + 4 commits só de documentação; o código é o aprovado). `main` preservada em
  `cde93c3cb4381481e50c57fd3180207f5c93c01f`; PR #9 em `bf8e2f15f54ce66d0b63f6e5b4bcca9a7a587108`.
- Último commit de implementação e documentação confirmado local e no remoto **antes de salvar este
  checkpoint**: `21075273ea5c0d32fe7462aec690e8ede64ba422`. Este checkpoint está em commit posterior
  (`git log -1 --format=%H -- docs/continuity/saas-backend.md`; entrega atual:
  `git ls-remote origin refs/heads/codex/saas-backend-render-neon-prep`).
- PR Draft #11 (base `codex/dev-preview-readiness`, empilhada para o diff mostrar só esta tarefa):
  <https://github.com/Lipe-meira/promo_bot/pull/11>. Diff:
  [codex/dev-preview-readiness...codex/saas-backend-render-neon-prep](https://github.com/Lipe-meira/promo_bot/compare/codex/dev-preview-readiness...codex/saas-backend-render-neon-prep).

| Commit | Conteúdo |
| --- | --- |
| `00ec00b` | URL do Neon/asyncpg com TLS verificado, pool Free, 503 `DATABASE_UNAVAILABLE` |
| `a1dbd9a` | migrations só no endpoint direto, com alvo confirmado (`-x confirm=<host>`) |
| `c596b93` | `promo-api-admin`: e-mail pela entrada padrão, `list` mascarado |
| `29e07b2` | teste de recuperação encerra só o backend do próprio engine |
| `4bbf558` | admissão exige e-mail primário verificado confirmado pelo Clerk; sem eco de entrada nos erros |
| `2c69cc5`, `e0d7982` | `render.yaml` (proposta) e seus testes; Python 3.12.16; CI observa `render.yaml` |
| `97b70ea` | engines de operador sem o `command_timeout` da API |
| `2107527` | documentação e revisão diferencial |

## Decisões que a retomada precisa conhecer

1. **Clerk:** `user.email_verified` = "tem *algum* e-mail verificado" (documentação); sem shortcode para o
   status do **primário**. A API consulta `GET /v1/users/{sub}` só na primeira admissão, depois do
   convite, fora de transação, falha fechada. `PROMO_API_CLERK_SECRET_KEY` (`sk_…`) é obrigatória.
2. **Render Free:** sem pré-deploy, sem shell; migrations **nunca** no build/start; health check
   `/healthz` (não acorda o banco); deploy manual (`autoDeployTrigger: "off"`).
3. **Neon:** TLS sempre verificado fora de loopback; para o primeiro teste usar o endpoint **direto**
   também na API; migrations só pelo direto.
4. **Mudanças de contrato:** `promo-api-admin invite|revoke` leem a entrada padrão (argumento posicional
   removido); nova variável obrigatória; erros de subida não repetem o valor rejeitado.

## Validações (17/11/2026)

| Verificação | Resultado |
| --- | --- |
| `apps/api`: `ruff format --check`, `ruff check`, `mypy` strict, `uv lock --check`, `openapi --check` | limpos |
| `apps/api`: `pytest` sem banco | 193 passed (baseline 73) |
| `apps/api`: PostgreSQL descartável (`scripts/test-postgres.ps1`) | 70 passed (baseline 57); contêiner removido |
| Raiz: `ruff format --check` (294 arquivos), `ruff check`, `mypy src` (104 arquivos), `uv lock --check --offline`, `git diff --check` | limpos |
| Simulação Linux (`python:3.12-slim`, comandos do `render.yaml`) | build sem dev; start em `0.0.0.0:$PORT`; `/readyz` 503→200 após migration do operador; sem migration no start; RSS ≈ 101 MB |
| Handshake TLS com CA descartável | CA desconhecida recusada; CA confiável conectou em TLS |
| `alembic -x confirm=…` (CLI real, hosts `.invalid`) | recusa pooler e alvo sem confirmação antes de conectar |
| Links locais e âncoras dos documentos editados | verificados (um script próprio) |
| Mutações | sem `pool_pre_ping` e sem a consulta ao Clerk são detectadas |
| Suíte offline do bot (`pytest -m "not live and not browser"`, raiz) | **1569 passed**, 5 deselected, 6 avisos de depreciação (13 min 56 s); nenhum arquivo do bot foi alterado |
| CI do GitHub no PR #11, head `21075273…` (`api` com PostgreSQL de serviço + `web`) | `api` **success** (36 s), `web` **success** (46 s) ([execução](https://github.com/Lipe-meira/promo_bot/actions/runs/44101409965)) |

Evidência RED/GREEN: URL do provedor reproduzida (`TypeError … 'sslmode'`) e 16 testes RED →
GREEN; ecos de segredo (3 RED); `command_timeout` herdado (1 RED). Os testes de admissão foram
escritos antes, mas a implementação foi feita antes de executá-los; a evidência equivalente é a
mutação (sem a consulta, 3 falham). Um erro meu em teste (encerrar todos os backends do banco) só
apareceu na suíte completa e foi corrigido.

Não executados: Playwright/navegador, `apps/web` (nada alterado; `openapi.json` inalterado), CodeQL/Semgrep
(sem scanner e com regras que exigem rede; o lint de segurança do Ruff rodou, 1 falso positivo revisado),
teste de carga, qualquer serviço real.

## Pendências e riscos (não declarar como feito)

- **Auditoria independente** antes de provisionar. Decisões abertas: R-1 (`/readyz` público consome
  CU-h do Neon), R-2 (recusa do Clerk sem limite), R-5 (nomes dos campos do payload do Clerk, falha
  fechada), R-6 (pooler Neon + asyncpg). Detalhes na revisão.
- Render real, Neon real e Clerk real nunca foram exercitados; disponibilidade do patch 3.12.16 no Render
  e o comportamento do Blueprint não foram verificados.
- `apps/web` não traduz `DATABASE_UNAVAILABLE`, `IDENTITY_PROVIDER_UNAVAILABLE` nem
  `PRIMARY_EMAIL_NOT_VERIFIED` (mensagem genérica); ajustar exige novo deployment da web.
- Checklist do que precisará ser autorizado depois: seção 10 de [SAAS_BACKEND_RENDER_NEON.md](../SAAS_BACKEND_RENDER_NEON.md#10-o-que-será-necessário-autorizar-depois).

## Próximos passos

Aguardar a auditoria; corrigir o que ela apontar na mesma branch. Só depois, com autorização específica
por etapa: projeto Neon → migration controlada → serviço Render e primeiro deploy manual → web Preview →
Clerk Development (claims) → convite do primeiro usuário e teste real no navegador.
