# Backend SaaS — preparação técnica para Render Free + Neon Free (17/11/2026)

> Estado: **preparado e validado offline.** Nada foi provisionado, implantado ou migrado: nenhum
> serviço Render, projeto Neon, convite, usuário ou deployment foi criado, e a instância Clerk real
> não foi consultada. **Não é uma aplicação pronta para produção.** Este documento complementa o
> [contrato da fundação SaaS](SAAS_WEB_FOUNDATION.md); o checkpoint está em
> [continuity/saas-backend.md](continuity/saas-backend.md).

Legenda das evidências: **verificado** = executado nesta tarefa; **documentação** = lido na
documentação pública do provedor em 17/11/2026, não testado numa conta real; **não validado** =
depende de recurso externo real.

## 1. Topologia alvo

Web (Vercel, protegido) → HTTPS → **FastAPI no Render Free** → TLS verificado → **PostgreSQL no
Neon Free**. Os tokens são emitidos pelo **Clerk Development**; a API os valida (RS256, JWKS) e
decide admissão e autorização no PostgreSQL. O bot (Telegram, AliExpress, workers) fica **fora**:
`apps/api` não importa o bot, tem `pyproject.toml` e `uv.lock` próprios, e testes garantem isso.

## 2. Render Free

### 2.1 Blueprint ([`render.yaml`](../render.yaml)) — proposta revisável

Commitar o arquivo **não cria nada**; os recursos só existem se o dono aplicar o Blueprint no painel
(autorização separada). Decisões:

| Item | Configuração | Motivo |
| --- | --- | --- |
| Serviço | um web service Python, plano `free`, raiz `apps/api` | sem banco nem worker no Blueprint |
| Deploy | `autoDeployTrigger: "off"` | push em branch nunca altera o serviço (documentação Render) |
| Build | `pip install uv==0.12.9 && uv sync --locked --no-dev` | instala só dependências de runtime, falha se o lock estiver desatualizado |
| Start | `.venv/bin/uvicorn promo_api.main:create --factory --host 0.0.0.0 --port $PORT` | Render exige bind em `0.0.0.0` e recomenda `$PORT` (padrão 10000); um worker |
| Health check | `/healthz` | **sem banco**: `/readyz` consulta o PostgreSQL e, sondado continuamente, manteria um Neon que escala a zero sempre acordado, consumindo as 100 CU-h do mês |
| Pré-deploy | **ausente** | `preDeployCommand` não existe em instâncias Free (documentação Render) |
| Migrations | **nunca** no build nem no start | ver [§5](#5-migration-controlada-fora-do-render-free) |
| Python | `PYTHON_VERSION=3.12.16` | patch exercitado na simulação Linux; a faixa suportada é `>=3.12,<3.13` |
| Ambiente | `PROMO_API_ENVIRONMENT=production` | só desliga o `/docs` interativo numa URL pública; não troca a instância Clerk |
| Região | `ohio` (proposta) | aproximar do projeto Neon; trocar as duas juntas |

Segredos são declarados com `sync: false`: o Render pergunta os valores no painel **apenas na criação
inicial do Blueprint**; depois, alterar/adicionar no painel. Nunca no Git, chat ou logs.

### 2.2 Variáveis de ambiente da API

| Variável | Obrigatória | Segredo | Observação |
| --- | --- | --- | --- |
| `PROMO_API_DATABASE_URL` | sim | sim | `postgresql+asyncpg://…` do Neon (ver [§3](#3-neon-free)) |
| `PROMO_API_CLERK_ISSUER` | sim | não | HTTPS da instância Clerk **Development** |
| `PROMO_API_CLERK_AUTHORIZED_PARTIES` | sim | não | origem(ns) exata(s) do web, sem `*` |
| `PROMO_API_CLERK_SECRET_KEY` | **sim (nova)** | sim | `sk_…` da **mesma** instância; só confirma o e-mail primário na admissão ([§6](#6-autenticação-clerk)) |
| `PROMO_API_ENVIRONMENT` | não | não | `production` no Render |
| `PROMO_API_CORS_ALLOWED_ORIGINS` | não | não | padrão: as origens autorizadas |
| `PROMO_API_DB_POOL_SIZE` / `_MAX_OVERFLOW` | não | não | padrão 3 / 2 ([§3.4](#34-pool-para-um-ambiente-gratuito)) |
| `PROMO_API_DB_POOL_TIMEOUT_SECONDS` / `_CONNECT_TIMEOUT_SECONDS` / `_POOL_RECYCLE_SECONDS` | não | não | padrão 10 / 15 / 240 |

Se o `/readyz` for usado pelo operador logo após um deploy, a primeira chamada é uma sondagem real.

Erros de validação na subida **não imprimem o valor rejeitado** (`hide_input_in_errors`): antes, uma
chave ou URL curta rejeitada era ecoada nos logs.

### 2.3 Limites do plano Free (documentação Render, 17/11/2026)

Spin-down após 15 min sem tráfego e retomada de cerca de um minuto; 750 horas Free por workspace
por mês; sistema de arquivos efêmero; **sem** shell/SSH nem one-off jobs; Render pode reiniciar a
instância a qualquer momento; 0,1 CPU / 512 MB (tabela de planos). Consequências: a primeira
requisição após inatividade espera o boot; a web deve tolerar esse atraso; nada que dependa de disco
local; operações administrativas (migrations, convites) rodam **fora** do Render.
**Verificado:** o processo da API usa cerca de 101 MB residentes (RSS/pico) após alguns requests,
inclusive sobre TLS — folga ampla contra 512 MB, medida **sem carga** (não é teste de carga).

### 2.4 Empacotamento

O wheel (`uv build`) contém `promo_api`, mas **não** `migrations/` nem `alembic.ini` (**verificado**).
A API lê as revisões esperadas de `apps/api/migrations`, ao lado do código-fonte; por isso o deploy
usa a instalação editável padrão do `uv sync` (a simulação confirmou `promo_api` carregado de
`/app/src` com `migrations/` ao lado). Uma imagem montada só com o wheel **recusa-se a subir**
(`shipped_heads` falha alto, coberto por teste) em vez de ficar eternamente em 503.

### 2.5 Simulação Linux do build e do start (verificado)

Contêiner limpo `python:3.12-slim` (Python 3.12.16), código extraído por `git archive` do commit,
**comandos de build e start lidos do próprio `render.yaml`**, PostgreSQL descartável em
loopback no mesmo namespace de rede:

- build: 31 distribuições, **nenhuma** ferramenta de desenvolvimento (pytest/ruff/mypy) e **nenhum**
  pacote do bot (telethon/playwright/aiogram/aiosqlite);
- start em `0.0.0.0:$PORT`; **sem migration no start**: com o banco vazio, `/healthz` 200,
  `/readyz` **503**, `/v1/me` sem token 401, `/docs` 404; após `alembic upgrade head` executado
  pelo operador, `/readyz` passa a **200** sem reiniciar;
- logs do servidor sem a senha do banco nem a chave secreta.

Não equivale a um deploy no Render: o ambiente do Render (imagem, `PYTHON_VERSION`, disponibilidade
do patch 3.12.16, comportamento do Blueprint) **não foi exercitado**.

## 3. Neon Free

### 3.1 Endpoint direto × pooler (documentação Neon)

O host com sufixo `-pooler` usa PgBouncer em **modo transação**; o host sem sufixo é direto. A
documentação indica pooler para aplicações web e **direto para migrations**. Neon informa suporte a
prepared statements de protocolo no PgBouncer ≥ 1.22, sem mencionar asyncpg.
**Proposta para o primeiro teste:** usar o endpoint **direto** também na API (uma instância, pool de
3+2): remove a incógnita asyncpg × PgBouncer. A configuração para o pooler existe (cache de statements
desligado, nomes únicos), mas **não foi validada contra um pooler real**. Os limites de conexão
do plano Free não constam nas páginas consultadas.

### 3.2 TLS e a string de conexão (verificado)

A string do Neon traz `?sslmode=require&channel_binding=require`. O SQLAlchemy repassa os parâmetros
da URL ao asyncpg como argumentos nomeados: o primeiro acesso falhava com
`TypeError: connect() got an unexpected keyword argument 'sslmode'` (reproduzido). Só remover o
parâmetro deixaria o asyncpg **sem verificar o certificado**. Agora:

| URL | Resultado |
| --- | --- |
| host remoto, `sslmode=require`/`verify-ca`/`verify-full` ou ausente | TLS **sempre com verificação** de cadeia e nome (`require` do libpq é elevado) |
| host remoto, `sslmode=disable`/`allow`/`prefer` | recusada na subida |
| loopback sem `sslmode` | sem TLS (banco local descartável) |
| `channel_binding`, `sslnegotiation` | removidos (libpq-only) |
| qualquer outro parâmetro (`options`, `ssl`, …) | recusado, **só o nome** aparece no erro |

Handshake real, com CA e certificado descartáveis (**verificado**): certificado de CA não confiável
→ `SSLCertVerificationError`; CA confiável + URL no formato Neon → conectado e `pg_stat_ssl.ssl = true`.
Não foi testado contra o Neon (o certificado público do Neon e as raízes do Render são **não validados**).

### 3.3 Migrations: só o endpoint direto, com alvo confirmado

`create_operator_engine(require_direct=True)` recusa o pooler e `confirm_target` exige
`-x confirm=<host>` para qualquer host que não seja local: protege contra variável exportada
apontando para o banco errado. Ambas as guardas rodam **antes** de qualquer conexão e não repetem o host.

### 3.4 Pool para um ambiente gratuito

| Configuração | Padrão | Motivo |
| --- | --- | --- |
| `pool_size` + `max_overflow` | 3 + 2 (era 5 + 5) | processo pequeno; poucas conexões simultâneas |
| `pool_pre_ping` | ligado | substitui conexão derrubada pelo Neon ao suspender |
| `pool_recycle` | 240 s | aposenta conexões antes dos 5 min de ociosidade do Free |
| `pool_timeout` | 10 s | espera limitada por conexão livre |
| `timeout` de conexão | 15 s (API, migrations e CLI) | acordar o compute leva instantes, mas a espera é limitada |
| `command_timeout` | 30 s **só na API** | consulta presa não segura conexão para sempre; migrations não o herdam (um índice grande pode passar de 30 s) |

### 3.5 Suspensão, retomada e indisponibilidade

Neon Free escala a zero após 5 min de inatividade (não configurável) e reativa em "algumas centenas
de milissegundos" (documentação). Falhas de conexão (recusada, tempo esgotado, derrubada, pool
saturado, falha de DNS ou de rota, e os erros do servidor com SQLSTATE de classe 08/53/57 — desligamento
administrativo, "cannot connect now", excesso de conexões) viram **503 `DATABASE_UNAVAILABLE` com
`Retry-After: 5`**, nunca 500 (**verificado**). Duas lacunas foram achadas na auditoria independente:
o asyncpg deixa escapar `OSError` sem encapsular e o dialeto traduz todo erro do servidor para um
`DBAPIError` genérico. Falha de certificado (`ssl.SSLError`) e erros de integridade ou sintaxe seguem
como 500 de propósito: repetir não os resolve.
Com conexões do servidor terminadas à força — o que um compute suspenso faz —, a API se recupera
no pedido seguinte; um pool saturado responde 503 em vez de pendurar (PostgreSQL descartável;
desligar `pool_pre_ping` faz o teste falhar). **Não validado** contra uma suspensão real do Neon.

### 3.6 Readiness

`/readyz` só é 200 se `alembic_version` registra exatamente os heads das migrations deste build
(inalterado, W-02); nunca migra. **Verificado** na simulação: 503 antes da migration, 200 depois.

O endpoint é público e consulta o banco, então o resultado é **guardado em cache por processo**: sucesso
por 30 min, falha por 5 s, uma só sondagem para chamadas concorrentes (`CachedReadiness`). Sem isso, quem
conhecesse a URL manteria o Neon acordado e consumiria as 100 CU-h do mês; com isso um fluxo contínuo o
acorda cerca de 14 % do tempo. Consequências: a primeira chamada após cada deploy é sempre real; logo
após **migrar**, o 200 aparece em até 5 s; um rollback de schema feito com o processo no ar pode ser
reportado como pronto por até 30 min (reinicie o serviço para forçar a leitura).

## 4. Estratégia de migrations: resumo

Render Free não tem pré-deploy nem shell; a API não migra no start. O operador migra **antes** de
liberar a nova versão, a partir de uma máquina confiável, pelo endpoint direto.

## 5. Migration controlada (fora do Render Free)

Executar **somente com autorização específica**, em banco criado para este fim. Nenhum passo abaixo
foi executado contra um banco externo.

```powershell
cd apps/api
# 1) URL DIRETA (sem "-pooler") sem deixá-la no histórico nem em argumentos:
$secure = Read-Host "URL direta do Neon" -AsSecureString
$env:PROMO_API_DATABASE_URL = [System.Net.NetworkCredential]::new('', $secure).Password
# 2) conferir o estado e o alvo (host digitado por você, sem copiar da variável):
uv run alembic -x confirm=<host-direto> current
# 3) aplicar:
uv run alembic -x confirm=<host-direto> upgrade head
# 4) limpar:
Remove-Item Env:PROMO_API_DATABASE_URL
```

Ordem: **migrar → conferir `GET /readyz` = 200 → só então liberar a API** (deploy manual). Uma
versão nova sobre banco não migrado responde 503 em `/readyz`; um rollback de código com banco já
migrado também (reverter schema é decisão explícita: `alembic downgrade`, nunca automática).
O host é comparado sem diferenciar maiúsculas; se divergir, nada conecta. `alembic -x database_url=…`
colocaria a senha em argumento de processo: use a variável de ambiente.

### 5.1 Preflight somente leitura (antes de qualquer migration)

`promo_api.preflight` (`apps/api/scripts/neon-preflight.ps1`) lê a URL só de `PROMO_API_DATABASE_URL`,
valida o **alvo antes de conectar** (driver `postgresql+asyncpg`, host exatamente o esperado e **não**
`-pooler`, banco e role) e então abre uma transação `READ ONLY` que confere banco, role, schema,
TLS, tabelas e `alembic_version`. A saída não contém URL, senha nem valores digitados. Códigos: 0 ok,
1 problema no estado, 2 alvo errado (nada conectou), 3 falha de conexão (só o tipo do erro).

| Estado | Significado | Próximo passo |
| --- | --- | --- |
| `empty` | schema sem tabelas | pode migrar (com autorização) |
| `migrated` | revisão `0001` e as 4 tabelas (`users`, `invitations`, `workspaces`, `workspace_memberships`) | nada a migrar |
| `outdated` | `alembic_version` abaixo do head | `upgrade head` com autorização |
| `unexpected` | tabelas sem `alembic_version`, ou conjunto divergente | **não migrar**; investigar |

O confirm do Alembic (`-x confirm=<host>`) cobre só o host; banco, role e schema são conferidos por este
preflight, que deve preceder o `upgrade`. Sobre `tls`: um banco em loopback reporta `false` (esperado);
no Neon, `false` é falha.

## 6. Autenticação Clerk

### 6.1 Contrato já verificado pela API (inalterado)

Assinatura **RS256** pelo **JWKS** do emissor (`alg=none`, HS256 e `kid` desconhecido recusados);
`iss` exato; `exp`/`nbf`/`iat` com tolerância de 5 s; `azp` na lista de origens; `sub` obrigatório.
A API **não** exige `sid` (quem exige é o cliente web, W-01). Papel e workspace nunca vêm do token:
admissão e memberships vêm do PostgreSQL.

### 6.2 A lacuna: `user.email_verified`

A documentação do Clerk descreve o shortcode como "Whether the user has a verified email address"
(**qualquer** endereço), e **não há** shortcode com o status de verificação do e-mail **primário**.
O token pode trazer `email = <primário>` e `email_verified = true` porque *outro* endereço está
verificado. A API admitia o convite do e-mail primário **sem confirmar que ele estava verificado**.
Se o Clerk Development consegue produzir esse estado (por exemplo, com verificação opcional no
cadastro) é **não validado**: depende de uma instância real. A admissão não pode depender disso.

### 6.3 Correção (menor segura, sem tocar o Clerk)

Na **primeira admissão**, depois de achar o convite ativo e fora de qualquer transação, a API consulta
a Backend API do Clerk (`GET /v1/users/{sub}`) e só admite se o e-mail do token for o endereço
**primário** do usuário **e** estiver com `verification.status == "verified"`.

| Situação | Resultado |
| --- | --- |
| primário verificado e igual ao do token | admitido |
| primário **não** verificado, outro endereço verificado (a lacuna) | 403 `PRIMARY_EMAIL_NOT_VERIFIED`, convite preservado, sem usuário criado |
| endereço verificado que não é o primário; sem primário; status `unverified`/`expired`/`failed` | 403 `PRIMARY_EMAIL_NOT_VERIFIED` |
| usuário inexistente no Clerk (404) | 403 `PRIMARY_EMAIL_NOT_VERIFIED` |
| rede, timeout, 401/403/429/5xx, redirecionamento, resposta fora do formato | **503** `IDENTITY_PROVIDER_UNAVAILABLE` + `Retry-After`, ninguém admitido |

A consulta ocorre **uma vez por usuário** (usuário já admitido nunca dispara chamada e não é afetado
por uma queda do Clerk) e **só para quem tem convite** (desconhecidos não geram tráfego à Backend API).
`PROMO_API_CLERK_SECRET_KEY` é obrigatória e precisa começar com `sk_`: **não existe modo** que admita
só pelas claims do token. Segredo e e-mail não vão a logs nem a erros; o id do usuário é codificado
no caminho; redirecionamentos não são seguidos.

### 6.4 O que continua não validado

- O formato real da resposta `GET /v1/users/{id}` numa instância viva. A documentação consultada
  confirma o status `verified`, o `primary_email_address_id` (no endpoint de atualização) e que a Backend API
  usa Bearer em `https://api.clerk.com/v1`; os nomes `email_addresses[].email_address` e
  `verification.status` seguem o tipo `BackendUser`/`EmailAddress`, **sem confirmação literal do
  JSON de resposta**. Se algum estiver errado, o efeito é **fechado**: ninguém é admitido
  (`PRIMARY_EMAIL_NOT_VERIFIED`), o que bloquearia o primeiro login — por isso o primeiro login real
  é um teste obrigatório.
- As claims `email`/`email_verified` do token continuam necessárias (a consulta confirma o e-mail
  alegado, não o substitui) e o limite de ~1,2 KB de claims customizadas continua válido.
- A semântica de `Clerk.session`/`addListener` do cliente web (W-01) segue sem instância real.
- Limites de taxa da Backend API não constam nas páginas consultadas; a chamada é uma por usuário.

## 7. Admissão inicial (`promo-api-admin`)

O e-mail é dado pessoal. `invite` e `revoke` o leem da **entrada padrão** (uma linha; prompt sem eco
em terminal), nunca de argumento (visível na lista de processos, no histórico e em logs de CI), e
nenhuma mensagem o repete. Um e-mail passado como argumento é recusado sem ser ecoado.
`list` mascara os endereços (`s***@example.test`); `--reveal` mostra tudo.

```powershell
# o endereço é digitado/colado no prompt sem eco; o banco vem da variável de ambiente:
uv run promo-api-admin invite
uv run promo-api-admin list              # mascarado
uv run promo-api-admin revoke
```

A CLI usa a mesma fábrica de engine (aceita a URL do provedor, recusa URL insegura sem ecoá-la).
**Nenhum convite real foi executado.** Mudança de contrato: `promo-api-admin invite <email>` não
funciona mais.

## 8. Validação executada (17/11/2026)

| Verificação | Resultado |
| --- | --- |
| `apps/api`: `ruff format --check`, `ruff check`, `mypy` (strict), `uv lock --check`, `openapi --check` | limpos |
| `apps/api`: `pytest` sem banco | **221 passed** (193 antes da auditoria independente; 73 na fundação) |
| `apps/api`: PostgreSQL descartável (`scripts/test-postgres.ps1`, contêiner removido) | **70 passed** (57 antes) |
| Semgrep `p/python` + `p/security-audit` + `p/secrets` (métricas desligadas), 24 arquivos | 0 achados |
| Simulação Linux de build/start/readiness/migration (contêiner descartável) | conforme [§2.5](#25-simulação-linux-do-build-e-do-start-verificado) |
| Handshake TLS com CA descartável | conforme [§3.2](#32-tls-e-a-string-de-conexão-verificado) |
| Lint de segurança ad hoc (`ruff --select S,B,ASYNC,PERF,SIM`) em `src/` | 1 achado, falso positivo revisado: chave sintética usada só para gerar o `openapi.json` |
| Mutações | sem `pool_pre_ping`: falha o teste de recuperação; sem a consulta ao Clerk: falham 3 testes de admissão |

Não executados: Playwright/navegador, `apps/web` (nenhum arquivo alterado; `openapi.json` inalterado),
qualquer serviço externo real (Render, Neon, Clerk), operações do bot.

## 9. Riscos residuais

1. Render real, Neon real e Clerk real **não foram exercitados** (itens “não validado” acima).
2. Pooler Neon + asyncpg não validado; usar o endpoint direto no primeiro teste.
3. Cold start de ~1 min do Render Free + wake do Neon: o primeiro acesso pode demorar e a web mostra
   mensagem genérica (ver 4).
4. `apps/web` ainda não traduz `DATABASE_UNAVAILABLE`, `IDENTITY_PROVIDER_UNAVAILABLE` nem o motivo
   `PRIMARY_EMAIL_NOT_VERIFIED`: cai em “Algo deu errado. Tente novamente.”. Ajuste pequeno, mas exige
   novo deployment da web (autorização separada). `openapi.json` não lista esses 503.
5. A consulta ao Clerk acrescenta dependência e um segredo na API; a admissão (não o uso diário) falha
   fechada se o Clerk estiver indisponível.
6. JWKS em cache por processo; cada reinício do Render Free busca de novo. CSP, ESLint e teste em
   navegador seguem pendentes (ver o contrato da fundação).
7. 750 h/mês Free por workspace Render e 100 CU-h Neon: ninguém mediu consumo; sem alerta de custo.
8. **`/readyz` é público e consulta o banco**: mitigado com cache (ver [§3.6](#36-readiness)); um fluxo
   contínuo acorda o Neon ~14 % do tempo (≈ 26 CU-h/mês). **Resta o amplificador autenticado**
   ([revisão](SAAS_BACKEND_DIFFERENTIAL_REVIEW.md), R-8): um token Clerk válido de quem não foi convidado
   custa 2 consultas por requisição e também acorda o banco. Recomendação para a Fase do Clerk: modo
   **Restricted** de cadastro (só convidados criam conta); limite de taxa antes de produção.
9. A recusa da consulta ao Clerk não é limitada por taxa (R-2 da revisão): uma identidade convidada com
   primário não verificado pode gerar uma chamada à Backend API por requisição.
10. Já mitigado, para constar: o corpo da requisição é limitado a 16 KiB antes da leitura (413
    `PAYLOAD_TOO_LARGE`; o FastAPI lê o corpo antes de autenticar e a instância Free tem 512 MB) e
    `/openapi.json` e `/docs` ficam desligados em `production`.

## 10. O que será necessário autorizar depois

1. Criar o **projeto Neon** (Free, região escolhida) e obter as URLs direta e pooler.
2. Aplicar `alembic upgrade head` nesse banco, com o procedimento da [§5](#5-migration-controlada-fora-do-render-free).
3. Criar o **serviço Render** (aplicar o Blueprint ou criar manualmente), informar os segredos no painel
   (incluindo `PROMO_API_CLERK_SECRET_KEY`) e liberar o **primeiro deploy manual**.
4. Definir `NEXT_PUBLIC_API_URL` e a origem da web em authorized parties/CORS; um novo **Preview** da web.
5. Configurar, na instância **Clerk Development**, as claims `email`/`email_verified` e confirmar o
   formato real do token e do payload da Backend API.
6. **Convidar** o primeiro usuário (`promo-api-admin invite`) e validar o fluxo completo no navegador.
7. (Opcional) ajustar mensagens da web para os novos códigos de erro.
8. Confirmar o modo **Restricted** de cadastro do Clerk Development (risco 8) e decidir se a recusa da
   consulta ao Clerk deve ter limite.
Nada disso é autorizado por este documento. Revisão de segurança do diff:
[SAAS_BACKEND_DIFFERENTIAL_REVIEW.md](SAAS_BACKEND_DIFFERENTIAL_REVIEW.md).
