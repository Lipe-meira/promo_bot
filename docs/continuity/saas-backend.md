# Checkpoint — preparação técnica do backend SaaS (17/11/2026)

Estado (24/11/2026): **piloto de desenvolvimento validado de ponta a ponta** (Clerk Development → API no
Render Free → Neon Free → workspace → dashboard na Vercel Preview; ver "Piloto — validação real" abaixo).
Antes disso, a auditoria independente (Claude Code, revisor separado somente leitura + verificações próprias)
foi feita em 17/11/2026: **nenhum bloqueador**; seis achados não bloqueantes corrigidos com TDD na mesma
branch (N1–N5, parte de N7), dois aceitos e documentados (N6, resto de N7). Ver "Auditoria independente" em
[SAAS_BACKEND_DIFFERENTIAL_REVIEW.md](../SAAS_BACKEND_DIFFERENTIAL_REVIEW.md). **É um piloto de
desenvolvimento, não produção:** Clerk Development, planos Free, um usuário, sem bot, sem workers.
Merge, deploy de produção e ativação de Telegram/AliExpress/workers seguem **não autorizados**.

A partir desta sessão o trabalho é conduzido no Claude Code (o `AGENTS.md` ainda fala em Codex; as regras
de Git, segurança e validação dele continuam valendo).
Contrato, runbook, riscos e checklist de autorizações: [SAAS_BACKEND_RENDER_NEON.md](../SAAS_BACKEND_RENDER_NEON.md);
revisão de segurança do diff: [SAAS_BACKEND_DIFFERENTIAL_REVIEW.md](../SAAS_BACKEND_DIFFERENTIAL_REVIEW.md).

## Autorização e limites

Autorizado e feito na fase offline (17/11): leitura, edição no escopo, testes offline com PostgreSQL
**descartável** (Docker local), simulação em contêiner local, commits, push da branch e PR Draft.

Autorizado **por etapa** e feito em 23/11 (cada uma valeu só para a operação indicada): migration `0001` no
Neon dev, leitura e depois alteração do Clerk Development (claims e Invite-only), serviço Render Free
(Blueprint), variável e Redeploy **Preview** na Vercel, origem extra em `PROMO_API_CLERK_AUTHORIZED_PARTIES`.
A admissão do primeiro usuário (`promo-api-admin invite`) foi autorizada em 23/11; a execução não foi
executada pelo dono, cujo terminal retornou `invited` e `pending` (ver "Admissão do primeiro usuário").

**Continua não autorizado e não feito:** planos pagos ou cartão, Clerk Production, merge, push na `main`,
alterar PR #9 ou PR #10, qualquer deploy além do Preview já feito, Telegram/AliExpress/workers/envios do bot.

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
- ~~Não verificado: patch `PYTHON_VERSION=3.12.16` no Render e `.venv/bin/uvicorn` no start.~~ **Resolvido
  (23/11):** o build do Render concluiu e o start `.venv/bin/uvicorn promo_api.main:create --factory` subiu
  (`/healthz` 200, `/readyz` 200).
- ~~Render real, Neon real e Clerk real nunca foram exercitados.~~ **Resolvido (23/11–24/11):** os três
  foram exercitados no piloto (ver abaixo). Continuam fora de escopo: Clerk Production, planos pagos e carga.
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
- **Preflight real (operador, Neon):** `TARGET OK`, `schema state=empty`, `revision: <none>`; reportou
  `tls=False`. Causa provável: `pg_stat_ssl` mede o backend, não o enlace do cliente (proxy do Neon). Corrigido:
  `client_tls_verified` (transporte asyncpg + contexto verificado) decide; `server_pg_stat_ssl` é informativo.
  Regressões: remoto sem TLS recusado antes de conectar; `pg_stat_ssl` não substitui a checagem do cliente.
  Verificado em PostgreSQL local com TLS e CA descartável: CA confiável → verificado; CA desconhecida →
  `SSLCertVerificationError`; sem verificação → falha.
- **Preflight real reexecutado no Neon (operador, 23/11/2026, após o commit `27bd47f`): PASSOU.**
  `TARGET OK`, `client_tls_verified=True`, `schema state=empty`, `revision: <none>; expected: 0001`.
  Isso valida conexão SQL somente leitura, TLS verificado, host direto, banco `neondb` e role
  `neondb_owner` (resultado relatado pelo operador; a saída não contém segredos). Banco **ainda vazio**.
- **Migration `0001` APLICADA (23/11/2026)** no schema `public` do `neondb` (projeto `promo-saas-dev`,
  endpoint direto), com autorização explícita do dono e executada por ele (`alembic -x confirm=<host> upgrade
  head`; a credencial nunca passou pelo assistente). Preflight final relatado pelo operador: `TARGET OK`,
  `client_tls_verified=True`, `schema state=migrated`, `revision: 0001; expected: 0001`, cinco tabelas
  (`alembic_version`, `invitations`, `users`, `workspace_memberships`, `workspaces`). Banco sem dados
  (nenhum convite/usuário). Autorização vale só para esta migration.
- **Etapa A — Clerk Development, só leitura (23/11/2026, autorizada; nada alterado):** app `Promo Bot`
  (`app_3MCaZ…`), instância Development, sem Production. Issuer confirmado pelo discovery público:
  `https://charming-bison-2072.clerk.accounts.dev` (JWKS em `/.well-known/jwks.json`). Achados:
  1. **Claims do session token = `{}`**: faltam `email` e `email_verified`, que a API exige
     (`auth/tokens.py`: `email` string e `email_verified is True`) → sem elas, todo login falha. Requer
     alteração (autorização própria): `{"email": "{{user.primary_email_address}}", "email_verified": "{{user.email_verified}}"}`;
     conferir tipo booleano em "Preview user" antes do primeiro login.
  2. **Access mode = Open** (qualquer pessoa cria conta): risco R-8; recomendado **Invite-only**. Nesse modo
     o primeiro usuário precisa ser criado/convidado no próprio Clerk (a admissão do Postgres continua
     separada). Alteração com autorização própria.
  3. Origem da web a autorizar (`azp`): candidata `https://promo-bot-dev.vercel.app` (docs de preview);
     **não confirmada**.
  Lista de usuários do Clerk não foi lida (dados pessoais). Secret key não foi vista nem solicitada.
- **Etapa A2 — alterações no Clerk Development APLICADAS (23/11/2026, autorizadas pelo dono):**
  1. Claims do session token = `{"email": "{{user.primary_email_address}}", "email_verified": "{{user.email_verified}}"}`
     (salvo; conferido após recarregar). O **tipo** de `email_verified` (booleano × texto) **não foi conferido**:
     o "Preview user" exigiria exibir o e-mail de um usuário. A API exige `email_verified is True`; confirmar no
     primeiro login real (decodificar o token localmente) e, se vier texto, ajustar o formato antes de seguir.
  2. Access mode = **Invite-only** (salvo; conferido após recarregar). O primeiro usuário deve ser criado ou
     convidado no próprio Clerk (Users → Create user / Invitations), com o mesmo e-mail que será admitido no
     Postgres (`promo-api-admin invite`).
  Nenhum usuário, convite, chave ou outro recurso foi criado.
- **Etapa B — serviço Render CRIADO e no ar (23/11/2026, autorizada):** Blueprint `promo-api-dev` (workspace
  My Workspace; repositório público por URL, sem conectar o GitHub), branch `codex/saas-backend-render-neon-prep`,
  sincronizado no commit `0bcc25a`. Serviço web `promo-api-dev` (`srv-dc23dfk9v7es73c5kdu0`), plano **Free**
  (o painel confirma "free instance"; nenhum pedido de cartão apareceu), região Ohio, URL
  `https://promo-api-dev-k6bj.onrender.com`. Segredos digitados pelo dono no painel (não vistos). Origem web
  autorizada: `https://promo-bot-pktag8w18-lipe-s-projects1.vercel.app` (deployment específico; um novo
  deployment da Vercel muda o `azp` e exige atualizar `PROMO_API_CLERK_AUTHORIZED_PARTIES`).
  Verificação externa: `/healthz` 200; **`/readyz` 200 `{"status":"ready","database":"ok"}`** (API fala com o
  Neon, schema em `0001`); `/v1/me` sem token 401; `/docs` e `/openapi.json` 404.
  Observações: o Blueprint sincroniza mudanças futuras do `render.yaml` nessa branch (deploy continua manual);
  o build do Render usou o commit `0bcc25a` (só docs depois do código aprovado).
- **Etapa C — Vercel (23/11/2026, autorizada), variável:** `NEXT_PUBLIC_API_URL` =
  `https://promo-api-dev-k6bj.onrender.com` criada no projeto `promo-bot-dev` (tipo Config, Production +
  Preview; valor público, conferido na listagem). `NEXT_PUBLIC_*` é embutida no build, então só vale em
  deployment novo; o deployment original (`a98b942`, 16/11) é **Production** e continua sem a variável. O projeto
  não tem Git conectado. `docs/DEV_FIRST_DEPLOY.md` registra que a exceção Production foi usada **uma vez**; o
  deployment novo foi feito como **Preview** (item seguinte), então a exceção não foi reutilizada.
- **Etapa C — deployment (23/11/2026), CONCLUÍDO:** Redeploy de `8FDdRzwJL` (mesmo fonte `a98b942`) no ambiente
  **Preview** (sem exceção Production), cache de build desmarcado; a Vercel exigiu verificação de identidade
  por código (feita pelo dono). Deployment `BLzneoRiA5SccKBwsk1RFF56oVUp` **Ready** (build ~33 s), URL
  `https://promo-bot-9w0ykalnf-lipe-s-projects1.vercel.app`. A URL fica atrás da Vercel Authentication, então o
  bundle e o login foram verificados pelo dono no navegador (ver "Piloto — validação real").
- **Render — origem autorizada (23/11/2026, autorizada):** `PROMO_API_CLERK_AUTHORIZED_PARTIES` passou a conter
  as duas origens (a antiga `pktag8w18` e a do Preview `9w0ykalnf`, separadas por vírgula). "Save and deploy" no
  painel; deploy Live em ~31 s, mesmo commit `0bcc25a`, trigger "Environment updated". `/readyz` 200 e
  `/v1/me` sem token 401 após o restart. Nenhuma outra variável foi alterada.
- **Admissão do primeiro usuário (23/11/2026, autorizada):** o e-mail do dono (não registrado aqui: repositório
  público) já existia no Clerk Development (criado em 17/11), então **nenhum convite foi criado no Clerk** (Invite-only bloqueia só novos
  cadastros). Para a admissão na API foi entregue `apps/api/scripts/neon-admin.ps1 -Command invite`
  (prompt protegido para a URL do banco; e-mail por stdin), adicionado em `1d08391`. O dono executou a
  admissão e relatou que o terminal retornou `invited` (saída do `invite`) e `pending` (estado do convite na
  listagem, antes de aceito; relato do dono, a saída não foi enviada ao assistente). Depois do primeiro login
  o estado esperado na listagem é `accepted`; isso não foi conferido. O login e a criação do workspace
  (24/11) são coerentes com a admissão, já que a API recusa quem não foi admitido.

## Piloto — validação real (24/11/2026, relatada pelo dono)

Veredito: **GO WITH NOTES** — fluxo de desenvolvimento validado; não é produção.

Validado pelo dono, no navegador, no Preview `https://promo-bot-9w0ykalnf-lipe-s-projects1.vercel.app`
(logado na Vercel):
1. Login pelo Clerk Development na web.
2. Criação do workspace **Teste** (autenticado na API do Render, persistido no Neon).
3. Renomeação para **Promo Bot Piloto**; o dashboard passou a exibir o novo nome (leitura após edição).

O que isso prova: web (`NEXT_PUBLIC_API_URL` embutida) → API (`azp` do Preview aceito) → Neon (escrita e
leitura) → Clerk (claims `email` e `email_verified` aceitos pela API; a API exige `email_verified is True`,
então o tipo booleano da claim ficou comprovado na prática). Encerra as pendências: tipo de `email_verified`,
origem `azp` candidata, deployment novo da web, origem extra no Render, primeiro usuário.

Limitações (não validadas):
- Evidência do navegador é relato do dono; o assistente não abre o Preview (Vercel Authentication). Não há
  captura nem decodificação do token no repositório (e não deve haver).
- Um usuário, um workspace; nenhum teste de carga, concorrência, segundo usuário ou recusa de não admitido
  em ambiente real (cobertos só por testes offline).
- Render Free dorme após 15 min (a primeira chamada leva ~50 s); Neon Free suspende o compute.
- Não validado: R-2 e R-5 da auditoria, tradução de `DATABASE_UNAVAILABLE`/`IDENTITY_PROVIDER_UNAVAILABLE`/
  `PRIMARY_EMAIL_NOT_VERIFIED` na web, Clerk Production, domínio próprio, backup/restauração do Neon.
- O deployment `a98b942` **Production** da Vercel segue sem a variável da API; o Preview é o ambiente de teste.
- Nada do bot foi tocado: Telegram, AliExpress, workers, listeners e envios seguem desligados.

## Próximos passos

Integração dos PRs #9, #10 e #11 (todos Draft): análise e ordem em "Integração dos PRs" abaixo. Cada merge,
deploy ou ativação do bot exige autorização própria; nenhum foi feito.

## Integração dos PRs (análise de 24/11/2026, somente leitura)

Topologia verificada com `git` e `gh` (nenhuma base foi alterada):

| PR | Branch → base | Commits à frente da base | CI no GitHub |
|---|---|---|---|
| #9 | `codex/workspace-runtime-spec` → `main` | 12 (`bf8e2f1`) | **nenhum** (só toca `src/`, `tests/`, `docs/`, `README.md`) |
| #10 | `feat/saas-web-foundation` → `main` | 10 (`a98b942`) | `api` e `web` verdes |
| #11 | `codex/saas-backend-render-neon-prep` → `codex/dev-preview-readiness` | 27 (`1d08391`) | `api` e `web` verdes na ponta `1d08391` |

- `codex/dev-preview-readiness` (`2803af7`) = #10 + 4 commits só de documentação, **não tem PR** e não está em
  `main`. #11 contém `a98b942` e `2803af7` (ambos ancestrais).
- #9 é independente de #10 e #11: nenhum arquivo em comum e `git merge-tree` entre #9 e a ponta de #11 não
  tem conflito. `apps/` não importa `promo_bot` (só menciona o nome em texto/teste de configuração).
- O único workflow é `saas-web.yml` (paths `apps/**`, `render.yaml`): **a suíte Python raiz
  (`pytest -m "not live and not browser"`, ruff, mypy) não roda no GitHub**; #9 fica sem verificação remota.
- `#10` altera `.gitignore` (arquivos do Next.js) fora de `apps/`.
- `main` **não tem proteção de branch**: nada exige CI verde ou revisão para um merge. Nenhum PR tem revisão
  registrada. O último comentário do #9 diz "aguardando reauditoria independente" (R-05, 14/11).

**PR #10 INTEGRADO à `main` (24/11/2026, autorizado só para o #10):** SHAs conferidos antes (main
`cde93c3`, ponta `a98b94224f2bbecce95fae5c1f15d420988cab00`); saiu de Draft e foi mergeado com **merge commit**
(`--match-head-commit`), sem apagar `feat/saas-web-foundation` (segue em `a98b942`). Merge commit
`865af06021a849f951aa76c4e9fbdfe2e2157553` = nova ponta da `main`, pais `cde93c3` e `a98b942`. Revisão final:
API 73 + 57 testes de Postgres, ruff, mypy, `openapi --check`; web typecheck, `check:api`, 138 testes e build,
todos passando. PR #11 (Draft, base `feat/saas-web-foundation`) e #9 (Draft) **não foram tocados**; o #11
ainda precisa de revisão contra a `main`.

Ordem segura proposta (o passo 2 já foi executado; os demais precisam de autorização):
1. ~~Retargetar #11~~ **FEITO em 24/11/2026 (autorizado):** a base do #11 passou de `codex/dev-preview-readiness`
   para `feat/saas-web-foundation`; o PR segue **Draft**. Motivo: a base antiga não tem PR e não está em `main`;
   mergear #11 nela não entregaria nada. O diff agora é de 41 arquivos (+4004/−50), todos em `apps/api`, `docs/`,
   `render.yaml` e `.github/workflows/saas-web.yml`, nenhum em `apps/web`. Nenhum merge nem exclusão de branch.
2. **Mergear #10 → `main`** com *merge commit* (não squash), para manter os SHAs de que #11 descende; o #11
   passa a mostrar 31 commits (27 dele + 4 de docs do `dev-preview-readiness`).
3. **Mergear #11 → `main`** depois do #10 (se a base foi `feat/saas-web-foundation`, o GitHub retargeta ao
   apagar o branch do #10).
4. **#9 é independente**: pode entrar antes ou depois, mas só após a reauditoria pendente e a suíte raiz
   rodada localmente (não há CI para ela).

Bloqueios da entrega: os três PRs seguem Draft; a suíte Python raiz não roda no GitHub (adicionar um workflow
é mudança à parte); #9 espera reauditoria; `main` sem proteção; o checkpoint e `docs/` versionam identificadores
de infraestrutura do piloto (projeto/endpoint Neon, id do serviço Render, issuer do Clerk): não são segredos,
mas o repositório é **público**, então decidir se isso fica antes de integrar.
