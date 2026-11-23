# Checkpoint — preparação técnica do backend SaaS (17/11/2026)

Estado: **auditado e corrigido offline; nada provisionado.** A auditoria independente (Claude Code,
revisor separado somente leitura + verificações próprias) foi feita em 17/11/2026: **nenhum
bloqueador**; seis achados não bloqueantes corrigidos com TDD na mesma branch (N1–N5, parte de N7), dois
aceitos e documentados (N6, resto de N7). Ver "Auditoria independente" em
[SAAS_BACKEND_DIFFERENTIAL_REVIEW.md](../SAAS_BACKEND_DIFFERENTIAL_REVIEW.md). **Próximo passo:
aguardar a autorização da Fase Neon.** Não declarar a aplicação pronta para produção.

A partir desta sessão o trabalho é conduzido no Claude Code (o `AGENTS.md` ainda fala em Codex; as regras
de Git, segurança e validação dele continuam valendo).
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
| `ecb1bf5` | auditoria N2: falha de DNS/rede e erros transitórios do servidor (SQLSTATE 08/53/57) → 503, não 500 |
| `5d28948` | auditoria N3/N5/N7: `hide_parameters`, `/openapi.json` desligado em produção, CLI sem traceback |
| `c838d7f` | auditoria N4: corpo da requisição limitado a 16 KiB antes da leitura (413) |
| `8c8599d` | auditoria N1: cache de `/readyz` (30 min sucesso / 5 s falha, sondagem única) |

O commit de implementação confirmado antes do checkpoint desta rodada é `8c8599d`; a documentação e este
checkpoint estão em commits posteriores (`git log -3`, `git ls-remote origin refs/heads/codex/saas-backend-render-neon-prep`).

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

### Rodada da auditoria independente (17/11/2026, verificações refeitas do zero)

| Verificação | Resultado |
| --- | --- |
| `apps/api`: `uv lock --check`, `ruff format --check` (40 arquivos), `ruff check`, `mypy` strict (19 arquivos), `openapi --check` | limpos |
| `apps/api`: `pytest` sem banco | **221 passed** (193 no início da sessão) |
| `apps/api`: PostgreSQL descartável | **70 passed**; contêiner removido (conferido) |
| Semgrep `p/python`+`p/security-audit`+`p/secrets`, métricas desligadas, 24 arquivos | 0 achados, 0 erros |
| Guardas das migrations com hosts `.invalid` (pooler, sem/errado `confirm`, TLS desligado, variável vazia) | todas recusadas; senha não vazou |
| Simulação Linux `python:3.12-slim` (3.12.16), comandos lidos do `render.yaml`, código por `git archive` | build sem dev/bot; `/healthz` 200, `/readyz` 503 sem banco, `/openapi.json` e `/docs` 404, POST 20 KiB sem token 413; log sem segredos |
| Sondas (offline) | DNS falho → era 500 (corrigido); POST anônimo de 20 MB era lido por inteiro antes do 401 (corrigido) |

RED antes de cada correção (motivo conferido): `unhandled error: OSError`/`DBAPIError`; `openapi.json`
200 em produção; `hide_parameters` falso; CLI propagava a exceção crua; 413 ausente; `/readyz` sem cache
(importação). Nos três testes Postgres que seguem a readiness passo a passo, o cache real os quebrou na
primeira execução; eles agora injetam um `CachedReadiness` sem TTL (o app real usa o padrão).

Não executados: CodeQL (não instalado), Playwright/`apps/web`, teste de carga, serviços reais.
A suíte offline do bot (raiz) **não foi reexecutada** nesta rodada: nenhum arquivo em `src/promo_bot`,
`tests/` ou configuração do bot foi alterado (só `apps/api`, `docs/`); a referência anterior é
1569 passed.

Evidência RED/GREEN da rodada anterior: URL do provedor reproduzida (`TypeError … 'sslmode'`) e 16 testes RED →
GREEN; ecos de segredo (3 RED); `command_timeout` herdado (1 RED). Os testes de admissão foram
escritos antes, mas a implementação foi feita antes de executá-los; a evidência equivalente é a
mutação (sem a consulta, 3 falham). Um erro meu em teste (encerrar todos os backends do banco) só
apareceu na suíte completa e foi corrigido.

Não executados: Playwright/navegador, `apps/web` (nada alterado; `openapi.json` inalterado), CodeQL/Semgrep
(sem scanner e com regras que exigem rede; o lint de segurança do Ruff rodou, 1 falso positivo revisado),
teste de carga, qualquer serviço real.

## Pendências e riscos (não declarar como feito)

- Auditoria independente: **feita** (ver acima). Abertos: R-2 (recusa do Clerk sem limite), R-5 (nomes dos
  campos do payload do Clerk: a documentação pública e o revisor confirmam `primary_email_address_id`,
  `email_addresses[].verification.status`; falha fechada se algo divergir), R-6 (pooler Neon + asyncpg),
  **R-8 (token Clerk de não convidado custa 2 consultas por requisição e acorda o Neon; recomendar o modo
  Restricted de cadastro no Clerk Development)**. R-1 (`/readyz`) está mitigado com cache.
- N6: quem apaga a conta no Clerk e volta com o mesmo e-mail fica com `INVITATION_ALREADY_USED`; no piloto,
  usar e-mail novo. `rebind` fica para antes de produção. N7: `confirm` valida só o host (projeto Neon novo,
  um banco) e a CLI de admissão não tem guarda de alvo.
- Não verificado: se o Render oferece o patch `PYTHON_VERSION=3.12.16` (a documentação diz que qualquer
  versão publicada é aceita; a imagem oficial do Python tem 3.12.16) e se `.venv/bin/uvicorn` no start
  funciona no ambiente do Render (a simulação Linux sim; o Render real não).
- Render real, Neon real e Clerk real nunca foram exercitados; disponibilidade do patch 3.12.16 no Render
  e o comportamento do Blueprint não foram verificados.
- `apps/web` não traduz `DATABASE_UNAVAILABLE`, `IDENTITY_PROVIDER_UNAVAILABLE` nem
  `PRIMARY_EMAIL_NOT_VERIFIED` (mensagem genérica); ajustar exige novo deployment da web.
- Checklist do que precisará ser autorizado depois: seção 10 de [SAAS_BACKEND_RENDER_NEON.md](../SAAS_BACKEND_RENDER_NEON.md#10-o-que-será-necessário-autorizar-depois).

## Piloto SaaS — TASK 01 (23/11/2026): preparar a conexão do Neon

Veredito: **GO WITH NOTES** (preparação offline concluída; conexão real **não** validada).

- Adicionado `promo_api.preflight` + `apps/api/scripts/neon-preflight.ps1` + `tests/test_preflight.py`
  (13 testes; RED por `ModuleNotFoundError`, depois GREEN). Procedimento: [§5.1](../SAAS_BACKEND_RENDER_NEON.md#51-preflight-somente-leitura-antes-de-qualquer-migration).
- Verificado contra PostgreSQL **descartável** local: estado `empty` → `migrated` após `alembic upgrade head`
  local; nada tocou o Neon. Revisão esperada `0001`; tabelas `users`, `invitations`, `workspaces`,
  `workspace_memberships` (+ `alembic_version`).
- **Metadados do Neon confirmados (23/11/2026, só leitura, console no navegador embutido com login do
  dono; nenhuma string de conexão aberta):** projeto `promo-saas-dev` (`plain-glitter-36317389`), AWS US
  East 2 (Ohio), plano Free, Postgres 18, branch padrão `production` (`br-red-block-bdt0i6wy`, única),
  1 banco `neondb` (owner `neondb_owner`), 1 compute `ep-rough-thunder-bdlqm78n` (0,25–2 CU, suspenso),
  sem restrição de IP. É confirmação de **metadados**, não de conexão SQL.
- **Não confirmado:** o host direto completo (o console só foi lido sem abrir "Connect", que exibe
  credencial). O operador digita o host da string direta (sem `-pooler`, começa por
  `ep-rough-thunder-bdlqm78n`); o preflight o compara. Esperado: role `neondb_owner`, banco `neondb`,
  schema `public`.
- Pendente de conexão real: TLS contra o certificado do Neon, identidade (banco/role/schema), estado do
  schema e a migration (esta exige autorização própria).

## Próximos passos

A auditoria terminou sem bloqueadores. Com autorização específica **por etapa** (aprovar uma não aprova a
seguinte): projeto Neon → migration controlada → serviço Render e primeiro deploy manual → web Preview →
Clerk Development (claims) → convite do primeiro usuário e teste real no navegador.
