# Checkpoint — preparação técnica do backend SaaS (17/11/2026)

Estado (24/11/2026): **piloto de desenvolvimento validado de ponta a ponta** (Clerk Development → API no
Render Free → Neon Free → workspace → dashboard na Vercel Preview; ver "Piloto — validação real" abaixo).
Antes disso, a auditoria independente (Claude Code, revisor separado somente leitura + verificações próprias)
foi feita em 17/11/2026: **nenhum bloqueador**; seis achados não bloqueantes corrigidos com TDD na mesma
branch (N1–N5, parte de N7), dois aceitos e documentados (N6, resto de N7). Ver "Auditoria independente" em
[SAAS_BACKEND_DIFFERENTIAL_REVIEW.md](../SAAS_BACKEND_DIFFERENTIAL_REVIEW.md). **É um piloto de
desenvolvimento, não produção:** Clerk Development, planos Free, um usuário, sem bot, sem workers.
**Código integrado (24/11/2026):** os PRs #10 e #11 estão na `main` (merge commits `865af06` e
`a28d72500e99e3cb6aa42e38f2a8f0367a82445f`), seguidos do #12 (checkpoint) e do #13 (CI da suíte raiz,
`bot-root`); ver "Integração dos PRs" e "CI e merges posteriores". Deploy de produção, merge do #9 e
ativação de Telegram/AliExpress/workers seguem **não autorizados**.

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

Autorizado e feito em 24/11, **cada um só para a operação indicada**: troca da base do #11 (primeiro para
`feat/saas-web-foundation`, depois para `main`, sempre Draft); merge do #10 e merge do #11 na `main`, ambos
com merge commit, `--match-head-commit` e branches preservadas.

**Continua não autorizado e não feito:** planos pagos ou cartão, Clerk Production, merge ou alteração do
PR #9, exclusão de branches (inclusive `codex/saas-backend-render-neon-prep`, que o Render usa), push direto na
`main`, qualquer deploy além do Preview já feito, Telegram/AliExpress/workers/envios do bot.

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
- **PR #11: MERGEADO na `main` em 24/11/2026** (merge commit `a28d72500e99e3cb6aa42e38f2a8f0367a82445f`, pais
  `865af06` e `9c446cb4e3c04f13aa568e98701d40b817846ed0`):
  <https://github.com/Lipe-meira/promo_bot/pull/11>. A branch `codex/saas-backend-render-neon-prep` foi
  **preservada** (remoto em `9c446cb…`): o Blueprint do Render a segue; **não apagar nem commitar nela** sem
  decidir antes o efeito no Render (ver "Pendências reais após o merge"). Esta atualização do checkpoint vai
  pela branch `docs/saas-pilot-merge-checkpoint`. O histórico abaixo (base `codex/dev-preview-readiness`) é da
  fase anterior ao merge.

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

## Pendências reais após o merge (24/11/2026)

Nada abaixo foi feito; cada item precisa de autorização própria quando mexe em serviço externo, `main` ou PR.

**Estado:** `main` = `49b3a5e7fb40b01a1bbaeb3d33b36c416a82d452` (contém #10, #11, #12 e #13; ver "CI e merges
posteriores"). Antes do #12 e do #13 a `main` estava em `a28d725` (só #10 e #11). Piloto em Render Free +
Neon Free + Clerk Development + Vercel Preview, um usuário, um workspace. Telegram, AliExpress, workers e envios
do bot desligados.

1. **Render x `main`:** o serviço `promo-api-dev` segue a branch `codex/saas-backend-render-neon-prep` e roda o
   commit `0bcc25a` (deploy manual; nenhum deploy após o merge). Código e `render.yaml` já estão na `main`, mas o
   Blueprint ainda aponta para a branch antiga. Decidir, com autorização, se o Render passa a seguir a `main`
   e quando redeployar; até lá a branch não pode ser apagada.
2. **PR #9** (`codex/workspace-runtime-spec` → `main`, Draft, head `bf8e2f1`, conferido no GitHub em 24/11/2026):
   **segue Draft e aguarda reauditoria independente** (último comentário: "Quarta reauditoria — R-05 corrigido
   (aguardando reauditoria independente)", 14/11). Não tem checks reportados: foi aberto antes do `bot-root`, e
   o GitHub só roda o workflow em novo evento do PR (push ou reabertura). Não tocado.
3. **CI:** ~~a suíte Python raiz não roda no GitHub~~ **resolvido pelo #13** (`bot-root`, ver abaixo). Resta:
   o gatilho `push` de `saas-web.yml` só vale para `feat/saas-web-foundation`, então `api` e `web` **não rodam
   em push na `main`** (só em PRs que tocam `apps/**`, `render.yaml` ou o próprio workflow); `bot-root` roda
   nos dois eventos. O #12 (só `docs/`) não teve nenhum check.
4. **`main` sem proteção de branch** (nada exige CI verde ou revisão); próxima tarefa. Nomes dos checks
   candidatos: `bot` (workflow `bot-root`), `api` e `web` (workflow `saas-web`).
5. **Repositório público com identificadores de infraestrutura** (projeto/endpoint Neon, id do serviço Render,
   issuer do Clerk) em `docs/`: não são segredos, mas decidir se ficam.
6. **Não verificado:** estado `accepted` do convite após o primeiro login (`neon-admin.ps1 -Command list`);
   recusa de usuário não admitido, segundo usuário, carga e concorrência em ambiente real; R-2, R-5 e R-6 da
   auditoria; backup/restauração do Neon.
7. **Web:** não traduz `DATABASE_UNAVAILABLE`, `IDENTITY_PROVIDER_UNAVAILABLE` nem `PRIMARY_EMAIL_NOT_VERIFIED`;
   corrigir exige novo deployment da Vercel. O deployment `a98b942` **Production** segue sem a variável da API.
8. **Fora do piloto:** Clerk Production, domínio próprio, planos pagos, ativação do bot.
9. **Limpeza opcional (não autorizada):** `feat/saas-web-foundation` e `codex/dev-preview-readiness` seguem no
   remoto; worktrees locais antigos em `F:\projetos\promo_bot_saas` e temporários.

## Painel — TASK 01: cadastro de canais do Telegram (24/11/2026)

Branch `codex/panel-telegram-channels` (worktree `F:\projetos\promo_bot_panel`), criada de `origin/main` em
`fce85633c65a517ca95a7bd4d93074e25237e297`. PR Draft; **nada mergeado, nada implantado, nenhuma migration
aplicada em banco real.** Escopo: só cadastro e persistência. Fora de escopo (próximas tarefas): ligação com o bot
local, iniciar/parar, histórico de ofertas, qualquer chamada ao Telegram ou à AliExpress.

**O que existe**
- Tela **Configurações** (`settings-screen.tsx`) ganhou o cartão `telegram-channels-card.tsx`: lista os canais de
  origem salvos, adiciona e remove, salva ou troca o destino de teste, mostra erros de validação e de regra em
  português (códigos estáveis, nunca o texto cru do servidor) e o aviso fixo de que **salvar não comprova acesso
  do bot** (`Status do acesso do bot: não verificado`). O que a tela mostra vem da resposta da API.
- API autenticada (`apps/api`), todas por membership (404 indistinguível para workspace alheio):
  `GET /v1/workspaces/{id}/telegram-channels`; `POST .../sources` (201); `DELETE .../sources/{chat_id}`;
  `PUT .../destination`. Resposta: `sources[]`, `destination|null`, `max_sources` (50) e
  `access_status: "not_verified"` (literal no contrato).
- Persistência PostgreSQL, migration **`0002`**: `telegram_source_channels` (PK `workspace_id + chat_id`, `CHECK`
  do formato, FK com `ON DELETE CASCADE`) e `telegram_destinations` (PK `workspace_id`: um destino por workspace,
  trocado no lugar). Unicidade só **dentro** do workspace: dois workspaces podem cadastrar o mesmo ID sem se ver.
- Contrato: `apps/api/openapi.json` regenerado; `apps/web/src/lib/api/schema.d.ts` regenerado (`check:api` em
  sincronia); cliente tipado com 4 métodos novos e `USED_OPERATIONS` atualizado; CORS passou a permitir `PUT` e
  `DELETE`; nova capability `manual_channel_registry` (disponível) — `channel_selection` (escolher entre canais de
  uma conta conectada) **continua indisponível**.

**Regras de ID (reaproveitadas do projeto)** — `src/promo_bot/config/schema.py` e `affiliate/shadow_delivery.py`
aceitam só `^-100[1-9][0-9]*$` (canal/supergrupo) tanto para origem quanto para destino. A API usa o mesmo formato
com limite de tamanho: `^-100[1-9][0-9]{0,14}$` (no máximo 19 caracteres), sem trim, sem `@usuario`, sem links,
sem IDs positivos de usuário e sem dígitos de outros alfabetos. O mesmo padrão está no `CHECK` do banco e na
validação do navegador (só para poupar uma ida; a API decide). **Decisão confirmada pelo dono (24/11/2026):** o
destino de teste é o `private_channel` do projeto (ID `-100…`), não uma conversa direta com um usuário (ID
positivo), como no bot local. Na tela ele se chama "Canal privado de teste", para não ser confundido com uma
conversa direta.

**Regras de negócio** (409, com códigos): `SOURCE_CHANNEL_DUPLICATE`, `SOURCE_CHANNEL_LIMIT` (50),
`CHANNEL_IS_DESTINATION` e `DESTINATION_IS_SOURCE` (um chat não é origem e destino); `SOURCE_CHANNEL_NOT_FOUND`
(404). Mutações travam a linha do workspace (`FOR UPDATE`), então limite e checagens cruzadas não sofrem corrida;
salvar o mesmo destino de novo não altera nada, nem `updated_at`.

**Plano de migração e implantação registrado no PR #15:** o `/readyz` compara a revisão do banco com a
cabeça das migrations do código. Portanto, **antes de implantar este código no Render, a `0002` precisa ser aplicada
no Neon** (com `alembic -x confirm=<host> upgrade head`, pelo endpoint direto, autorização própria); sem isso a API
nova ficaria em 503 `NOT_READY`. O serviço atual do Render roda o commit `0bcc25a` (código anterior), então segue
funcionando enquanto nada for implantado. A web também só vê a tela nova num deployment novo da Vercel.

**Validação (offline, dados sintéticos, PostgreSQL descartável em Docker, porta local, removido ao final)**

| Verificação | Resultado |
| --- | --- |
| `apps/api`: `ruff format --check`, `ruff check`, `mypy` strict, `python -m promo_api.openapi --check` | limpos |
| `apps/api`: `pytest` sem banco | 245 passed |
| `apps/api`: `pytest -m postgres` (migrations reais, `command.check` sem divergência modelo × migration, ida e volta) | 103 passed, 33 deles novos em `test_telegram_channels_postgres.py` |
| `apps/web`: `typecheck`, `check:api`, `vitest` | limpos; 149 passed (eram 138) |
| `apps/web`: `next build` com valores sintéticos | compilou; `/settings` presente |
| Raiz: `uv lock --check --offline`, `ruff format --check`, `ruff check` | limpos; `src/promo_bot` não foi alterado |
| Mutações | sem o escopo de membership, o teste de isolamento falha; sem o limite de 50, o teste do limite falha |

Cobertura dos testes: persistência e releitura em ordem; remoção persistida; troca e idempotência do destino;
16 IDs inválidos (vazio, `-100`, zero à esquerda, positivo, grupo básico, `@`, link, espaços, `\n` final, sufixo,
dígitos largos, longo demais, número JSON, `null`) recusados com 422 sem eco da entrada e sem escrever; ID inválido
no caminho; campo desconhecido (`workspace_id` no corpo) recusado; duplicata e 5 cadastros concorrentes do mesmo ID
(exatamente um 201); limite; origem × destino; restrições do banco; isolamento (GET/POST/DELETE/PUT alheios dão a
mesma resposta de workspace inexistente, sem vazar IDs); mesmo ID em dois workspaces; cascata ao apagar o
workspace; na web, tela vazia, adicionar/remover/trocar com releitura após "recarregar a página", validação antes de
enviar, erros em português, limite, nova tentativa de carga isolada e troca de usuário.

**Correções da revisão do PR #15 (24/11/2026):** (1) o campo de origem só é limpo depois de sucesso; em recusa
(duplicata, limite, indisponibilidade) o ID digitado permanece. (2) O cartão aceita **uma alteração por vez**:
durante uma chamada em andamento, remover, adicionar e salvar destino ficam bloqueados (também por guarda
síncrona), então resposta atrasada não sobrescreve dado mais novo. (3) A API lê origens e destino em **uma única
instrução SQL** (`UNION ALL`), isto é, de um mesmo snapshot; antes eram duas consultas que podiam atravessar um
commit concorrente. Testes: 2 na web (ID preservado/limpo; controles bloqueados e nenhuma segunda chamada
enviada) e 2 na API (uma instrução por leitura; leitores concorrentes com escritas nunca veem um chat como
origem e destino). As duas da web e a de uma instrução falham no código anterior; o teste de leitores concorrentes
passa também no código anterior (é uma guarda de invariante, não prova da correção). Resultados: web 151 testes,
API 245 sem banco e 105 com PostgreSQL descartável.

**Limitações:** nenhuma checagem de que o bot enxerga o canal (é a próxima etapa); IDs digitados à mão, sem busca;
um destino por workspace e sem remoção de destino; sem verificação no navegador contra a API real (a tela foi
exercitada com um falso da API e jsdom); a ordem TDD não foi estrita: a implementação da API foi escrita antes dos
testes, e a prova de que eles pegam regressões veio das duas mutações acima. Não foram executados: suíte raiz do
bot (nada do bot mudou; roda no CI `bot-root`), Playwright, teste de carga.


## Atualização do painel piloto em 25/11/2026

- Base de código e SHA verificado no GitHub `ed09e2f54f1099cf97a186df77355b90a6d013ef` (`main`, merge do PR #15). A branch de origem já usada pelo Render, `codex/saas-backend-render-neon-prep`, avançou por fast-forward de `9c446cb` para esse mesmo commit. O serviço continuou com deploy manual.
- O [Preview da Vercel](https://promo-bot-d8m6u5l4i-lipe-s-projects1.vercel.app/sign-in) ficou `READY` com os arquivos da web do SHA acima. Os 48 arquivos enviados foram comparados por hash e a tela de login respondeu HTTP 200.
- A migração `0002` foi aplicada ao Neon com o SQL gerado pelo Alembic para `0001:0002`, em transação com verificação da revisão inicial. Uma consulta posterior confirmou `0002` e a presença de `telegram_source_channels` e `telegram_destinations`.
- A atualização da configuração do Render acrescentou o endereço do novo Preview às origens autorizadas do Clerk, preservando as duas origens registradas no piloto. A operação enfileirou um deploy de configuração do commit `9c446cb` antes do deploy do código novo.
- O [deploy do código novo no Render](https://dashboard.render.com/web/srv-dc23dfk9v7es73c5kdu0/deploys/dep-dc3dvhp9n9bc73d1aijg) ficou `live` às 10h26 BRT no SHA `ed09e2f54f1099cf97a186df77355b90a6d013ef`. O Render registrou o término com sucesso, a inicialização do Uvicorn e respostas HTTP 200 de `/healthz` após a troca.
- O Preview respondeu HTTP 200 em `/sign-in`. O acesso direto à API a partir do executor estava indisponível. A sondagem externa de `/readyz` e a gravação de um cadastro pelo Preview com login permanecem sem comprovação nesta execução.

### Deploy manual da `main` no domínio principal

- A pedido do dono, a Vercel publicou o mesmo SHA `ed09e2f54f1099cf97a186df77355b90a6d013ef` no ambiente `Production` do projeto de desenvolvimento. O [deploy](https://vercel.com/lipe-s-projects1/promo-bot-dev/dpl_6xtnb1HfMCCJ3o4HERCRtnmnzJ48) ficou `READY`. Os hashes dos 48 arquivos enviados conferem com os da `main`.
- O [domínio principal](https://promo-bot-dev.vercel.app/sign-in) foi associado ao deploy, e a tela de login respondeu HTTP 200 nesse domínio. A proteção Vercel Authentication continua aplicada a todos os deployments. A publicação foi manual.
- O [novo deploy da API](https://dashboard.render.com/web/srv-dc23dfk9v7es73c5kdu0/deploys/dep-dc3edhm7bikc73cv5t60) acrescentou `https://promo-bot-dev.vercel.app` às origens autorizadas. Ficou `live` no mesmo SHA, com `/healthz` respondendo HTTP 200. O teste de gravação com a sessão do dono continua pendente.

### Origem Git `main` no deploy de Production

- O [deploy de Production com origem Git](https://vercel.com/lipe-s-projects1/promo-bot-dev/dpl_ChGJiDQdSH9VmsnpFJnbKEmE7LMw) ficou `READY`. A Vercel registrou a clonagem de `Lipe-meira/promo_bot` na branch `main`, no mesmo SHA `ed09e2f54f1099cf97a186df77355b90a6d013ef`, com build de `apps/web` e rota `/settings`.
- A Vercel confirmou `main` no filtro de branches. O [domínio principal](https://promo-bot-dev.vercel.app/sign-in) foi associado ao novo deploy e respondeu HTTP 200 na tela de login. A publicação foi disparada manualmente a partir da referência Git.

Rastreabilidade deste registro na branch `codex/panel-pilot-rollout`, criada do SHA de código acima e publicada no GitHub. Consulte o histórico da branch para o SHA do próprio registro.

## CI e merges posteriores (24/11/2026)

Estado conferido no GitHub (`gh`) em 24/11/2026, depois do #13:

| PR | Estado | Merge commit | Branch (preservada) |
| --- | --- | --- | --- |
| [#12](https://github.com/Lipe-meira/promo_bot/pull/12) (checkpoint, só `docs/`) | MERGED 17:33 UTC | `02fa0d648789d6eb034649fc06e1510df77ce5d0` (pais `a28d725`, `bfe887f`) | `docs/saas-pilot-merge-checkpoint` em `bfe887f…` |
| [#13](https://github.com/Lipe-meira/promo_bot/pull/13) (workflow `bot-root`) | MERGED 17:51 UTC | `49b3a5e7fb40b01a1bbaeb3d33b36c416a82d452` (pais `02fa0d6`, `17eea56`) | `ci/promo-bot-root` em `17eea56e07c4aa769444649dbd434284d378537f` |
| [#9](https://github.com/Lipe-meira/promo_bot/pull/9) | **OPEN, Draft**, base `main` | — | `codex/workspace-runtime-spec` em `bf8e2f1…`; **aguarda reauditoria**; sem checks |

Os dois merges usaram merge commit e `--match-head-commit`, sem apagar branches. A branch do Render
`codex/saas-backend-render-neon-prep` segue em `9c446cb4e3c04f13aa568e98701d40b817846ed0`.

**Workflow `bot-root` (`.github/workflows/bot-root.yml`)**, separado de `saas-web.yml`: gatilhos
`pull_request` com destino à `main` e `push` na `main`, sem filtro de caminhos; job `bot` em Ubuntu com
Python 3.12, `uv lock --check`, `uv sync --locked`, `ruff format --check .`, `ruff check .`,
`mypy --platform win32 src` e `pytest -m "not live and not browser"`; sem segredos nem deploy; o bloqueio de rede
dos testes (`--disable-socket`, só loopback) vem do `pyproject.toml` e não foi alterado.
Duas correções saíram da primeira execução: o `mypy` falhava no Linux (16 erros, `msvcrt`/`windll` atrás de
`os.name == "nt"`), então roda como `win32`, a plataforma do desenvolvedor; e dois testes de
`tests/unit/test_aliexpress_short_links.py` comparavam com igualdade exata um timeout calculado do relógio
do loop (2.0 no Windows, ligeiramente menor no Linux), agora com `pytest.approx(…, abs=0.5)`. O `src/` não mudou.

| Check | Evidência | Resultado |
| --- | --- | --- |
| `bot` no PR #13 (SHA `17eea56e07c4aa769444649dbd434284d378537f`) | [execução 45452007038](https://github.com/Lipe-meira/promo_bot/actions/runs/45452007038), 4 min 10 s | **success**: 1549 passed, 20 skipped, 5 deselected |
| `bot` no **primeiro push na `main`** (SHA `49b3a5e7fb40b01a1bbaeb3d33b36c416a82d452`) | [execução 45453168142](https://github.com/Lipe-meira/promo_bot/actions/runs/45453168142), evento `push` | **success**: Ruff e `mypy` limpos, 1549 passed, 20 skipped, 5 deselected (5 min 38 s) |
| `api` e `web` (`saas-web`) | último run no PR #11, ponta `9c446cb` ([execução 45444763107](https://github.com/Lipe-meira/promo_bot/actions/runs/45444763107)) | **success** (40 s e 42 s) |

Observações sobre os checks: o `bot-root` por push em `main` rodou só uma vez (no #13); `api` e `web` **não** têm
execução em push na `main`, e o #12 não disparou nenhum workflow. Não há, portanto, `api`/`web` verdes medidos
sobre a ponta atual da `main`; o conteúdo de `apps/**` na `main` é o mesmo do #11 (nada em `apps/` mudou depois).

**Validação em Windows (execução local, não GitHub):** o CI roda só em Linux. O dono do projeto desenvolve em
Windows, então a suíte raiz foi rodada localmente em Windows no worktree `F:\projetos\promo_bot_ci`, que então
continha o workflow sobre `02fa0d6` (antes das duas correções): `pytest -m "not live and not browser"` =
**1569 passed, 5 deselected** (11 min 1 s). Depois das correções foram reexecutados localmente só
`tests/unit/test_aliexpress_short_links.py` (95 passed), `ruff format --check`, `ruff check` e
`mypy --platform win32 src` (limpos). A suíte completa **não foi reexecutada** em Windows no SHA final
(`17eea56`/`49b3a5e`); a diferença entre os dois estados é só o workflow e duas asserções com tolerância. Os 20
testes pulados no Linux não foram pulados no Windows (a execução local não teve nenhum skip); há testes
marcados `windows_only` (por exemplo em `tests/unit/test_runtime_migration_safety.py`), mas a origem de cada um dos 20
não foi levantada.

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

**Base do PR #11 trocada para `main` (24/11/2026, autorizado só para esta troca; segue Draft):** o diff contra
a `main` (`865af06`) tem 41 arquivos (+4016/−50), idêntico ao diff de três pontos, e a árvore da `main` é a
mesma da ponta do #10 (`a98b942`), então só restam as 35 mudanças do #11 (`apps/api`, `docs/`, `render.yaml`
e `.github/workflows/saas-web.yml`; nenhum arquivo em `apps/web`). A troca de base **não disparou CI** (o
workflow só escuta `opened/synchronize/reopened`); a combinação foi validada em worktree temporário
(`origin/main` + merge da ponta `8d9fe1a`; árvore resultante `ad0818a2…` = árvore da ponta do #11):
ruff format/check, mypy, `openapi --check`, 241 testes offline e 70 de Postgres (container descartável local),
todos passando. `apps/web` é idêntico à `main`; o job `web` verde em `8d9fe1a` cobre esse lado. Observação:
o gatilho `push` do workflow só vale para `feat/saas-web-foundation`, então depois do merge a `main` não roda
o CI no push (só em PRs); decidir se inclui `main` é mudança à parte. Nada mergeado nessa etapa. Depois do push
do checkpoint, o CI rodou com base `main` (`api` 40 s, `web` 42 s, ambos verdes).

**PR #11 INTEGRADO à `main` (24/11/2026, autorizado só para o #11):** SHAs conferidos antes (main
`865af06021a849f951aa76c4e9fbdfe2e2157553`, ponta `9c446cb4e3c04f13aa568e98701d40b817846ed0`); saiu de Draft e foi
mergeado com **merge commit** (`--match-head-commit`), sem apagar a branch. Merge em 2026-11-24 17:09 UTC; merge
commit `a28d72500e99e3cb6aa42e38f2a8f0367a82445f` = nova ponta da `main`, pais `865af06` e `9c446cb`. Branch
`codex/saas-backend-render-neon-prep` segue em `9c446cb…` (Render). #9 não foi tocado; nenhum deploy.

Ordem proposta (os passos 1 a 3 foram executados; o 4 segue pendente e precisa de autorização). O texto abaixo é
o histórico da análise:
1. ~~Retargetar #11~~ **FEITO em 24/11/2026 (autorizado):** a base do #11 passou de `codex/dev-preview-readiness`
   para `feat/saas-web-foundation`; o PR segue **Draft**. Motivo: a base antiga não tem PR e não está em `main`;
   mergear #11 nela não entregaria nada. O diff agora é de 41 arquivos (+4004/−50), todos em `apps/api`, `docs/`,
   `render.yaml` e `.github/workflows/saas-web.yml`, nenhum em `apps/web`. Nenhum merge nem exclusão de branch.
2. ~~Mergear #10 → `main`~~ **FEITO** (merge commit `865af06`).
3. ~~Mergear #11 → `main`~~ **FEITO** (merge commit `a28d725`; a base foi trocada para `main` antes, sem
   apagar branches).
4. **#9 é independente**: pode entrar antes ou depois, mas só após a reauditoria pendente e a suíte raiz
   rodada localmente (não há CI para ela).

Bloqueios identificados na análise (situação atual em "Pendências reais após o merge"): #10 e #11 já
integrados; #9 segue Draft e espera reauditoria; a suíte Python raiz não roda no GitHub; `main` sem proteção; o
checkpoint e `docs/` versionam identificadores de infraestrutura do piloto, num repositório **público**.
