# SaaS web — primeira entrega (fundação)

> Estado: **implementado e testado com infraestrutura sintética**. O fluxo completo com um
> Clerk real **não foi validado** (ver [Bloqueios](#bloqueios-e-o-que-depende-de-configuração-externa)).
> Este trabalho é independente da S1 (`WorkspaceRuntimeSpec`, PR #9) e não presume sua aprovação:
> nada aqui importa `promo_bot` nem toca o runtime SQLite do bot.

## O que existe

Fluxo: **login (Clerk) → criar workspace → salvo no PostgreSQL → dashboard → editar
configurações → recarregar e ver o valor persistido.**

| Camada | Local | Tecnologia |
| --- | --- | --- |
| API (control plane) | `apps/api/` | FastAPI, SQLAlchemy 2 async, asyncpg, Alembic, PyJWT |
| Web | `apps/web/` | Next.js 16 (App Router), React 19, TypeScript, `@clerk/nextjs` 7 |
| Contrato HTTP | `apps/api/openapi.json` | gerado dos modelos Pydantic; vira `apps/web/src/lib/api/schema.d.ts` |

Fora de escopo, de propósito: sessões Telegram, credenciais AliExpress (não há cofre), relay,
workers, publicação, histórico de promoções e qualquer dado fictício. Esses recursos aparecem na
interface como **indisponíveis, com o motivo** (`/capabilities`); o status nunca mostra algo
"online" sem heartbeat real (hoje não existe heartbeat).

## Endpoints

| Método e rota | Função |
| --- | --- |
| `GET /healthz` | liveness, sem banco |
| `GET /readyz` | readiness: banco migrado (`alembic_version`) |
| `GET /v1/me` | perfil local + workspaces do chamador; admite o usuário se houver convite |
| `POST /v1/workspaces` | cria workspace (nome, fuso); 409 se a conta já tem um |
| `GET /v1/workspaces/{id}` | lê (somente membro) |
| `PATCH /v1/workspaces/{id}` | atualiza com `version` obrigatório (concorrência otimista) |
| `GET /v1/workspaces/{id}/status` | estado real: nada provisionado, nada conectado |
| `GET /v1/workspaces/{id}/capabilities` | recursos e motivos de indisponibilidade |

Erros: envelope único `{"error": {"code", "message", "details?"}}` com códigos estáveis
(`AUTH_INVALID_TOKEN`, `AUTH_TOKEN_EXPIRED`, `AUTH_KEYS_UNAVAILABLE`, `NOT_ADMITTED`,
`WORKSPACE_NOT_FOUND`, `WORKSPACE_ALREADY_EXISTS`, `WORKSPACE_VERSION_CONFLICT`,
`VALIDATION_ERROR`, `NOT_READY`). Mensagens nunca ecoam entrada, token ou valores do banco.

## Decisões de segurança

- **Identidade vs. autorização.** O Clerk prova *quem* é. A API verifica o JWT (RS256 pelo JWKS do
  emissor, `iss`, `exp`/`nbf`/`iat`, `azp` na lista de origens autorizadas; `alg=none`, HS256 e
  `kid` desconhecido são recusados). *Se pode usar* e *o que pode tocar* vêm do PostgreSQL:
  `users`, `invitations`, `workspace_memberships`. Nada do navegador (id de usuário, papel,
  workspace) é lido; as proteções de rota do Next são só conforto de UX (`src/proxy.ts`).
- **Admissão privada (invite-only).** Um usuário só passa a existir localmente se houver convite
  ativo para o e-mail **verificado** do token. Convites são criados apenas pela CLI de operador
  (`promo-api-admin invite|revoke|list`); não há endpoint de convite. Revogar desabilita o usuário;
  convidar de novo reativa. O e-mail de um convite usado não pode ser assumido por outra identidade.
- **Isolamento.** Toda consulta de workspace passa pelo `JOIN` com a membership do chamador. Workspace
  de outro usuário e inexistente produzem a **mesma** resposta 404.
- **Sem modo inseguro.** Não existe configuração que desligue a autenticação; sem emissor e
  `azp` válidos o processo não inicia (um teste varre os nomes das configurações).
- **Sem cache compartilhado.** A API responde `Cache-Control: no-store` e `Vary: Authorization`;
  o cliente usa `cache: "no-store"` e pede um token novo a cada chamada; não há SWR nem cache de
  módulo; o provedor de sessão é re-criado ao trocar de usuário (`key={userId}`).
- **Concorrência.** Criação repetida/simultânea: `UNIQUE(owner_user_id)` decide (1×201, demais
  409 apontando o workspace existente). Atualização: `UPDATE … WHERE version = :v`; uma só vence.
- **Decisões reversíveis do MVP:** um workspace por dono (constraint única, removível por migration),
  papel único `owner`.

## Configuração (sem segredos no Git)

API — variáveis `PROMO_API_*` (nada é lido das variáveis do bot):

| Variável | Obrigatória | Descrição |
| --- | --- | --- |
| `PROMO_API_DATABASE_URL` | sim | `postgresql+asyncpg://…` |
| `PROMO_API_CLERK_ISSUER` | sim | URL `https://` da Frontend API da instância Clerk |
| `PROMO_API_CLERK_AUTHORIZED_PARTIES` | sim | origens do web app, separadas por vírgula (`http` só em localhost) |
| `PROMO_API_CORS_ALLOWED_ORIGINS` | não | padrão: as origens autorizadas |
| `PROMO_API_CLERK_JWKS_URL` | não | padrão: `<issuer>/.well-known/jwks.json` |
| `PROMO_API_ENVIRONMENT` | não | `development`, `test` ou `production` (em produção `/docs` fica desligado) |

Web — ver `apps/web/.env.example`: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`,
`NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL`, `NEXT_PUBLIC_API_URL`.

## Como executar localmente

Pré-requisitos: Python 3.12 + `uv`, Node ≥ 20.9, Docker (para o PostgreSQL).

```powershell
# 1) Testes sem banco e checagens (apps/api)
cd apps/api
uv sync
uv run ruff format --check . ; uv run ruff check . ; uv run mypy
uv run python -m promo_api.openapi --check
uv run pytest

# 2) Testes PostgreSQL: sobem um contêiner descartável (127.0.0.1, porta aleatória, tmpfs) e o removem
./scripts/test-postgres.ps1

# 3) Web (apps/web)
cd ../web
npm ci
npm run check:api ; npm run typecheck ; npm test
npm run build        # exige as variáveis do .env.example (valores sintéticos bastam para compilar)
```

Para rodar de verdade (precisa de uma instância Clerk **sua**, de desenvolvimento):

```powershell
cd apps/api
$env:PROMO_DEV_DB_PASSWORD = "<senha local>"; docker compose -f compose.dev.yaml up -d
$env:PROMO_API_DATABASE_URL = "postgresql+asyncpg://postgres:$env:PROMO_DEV_DB_PASSWORD@127.0.0.1:5433/promo_api_dev"
$env:PROMO_API_CLERK_ISSUER = "https://<sua-instancia>.clerk.accounts.dev"
$env:PROMO_API_CLERK_AUTHORIZED_PARTIES = "http://localhost:3000"
uv run alembic upgrade head                         # só neste banco de desenvolvimento
uv run promo-api-admin invite voce@exemplo.com      # convida o seu e-mail
uv run uvicorn promo_api.main:create --factory --port 8000
# em outro terminal: apps/web com .env.local preenchido, depois `npm run dev`
```

Regerar o contrato após mudar um schema da API: `uv run python -m promo_api.openapi` e, em
`apps/web`, `npm run gen:api` (o CI falha se algum dos dois estiver desatualizado).

## Critérios de aceite e como verificar

| # | Critério | Verificação |
| --- | --- | --- |
| 1 | Token ausente, malformado, forjado, expirado ou de outra origem é recusado em toda rota `/v1` | `tests/test_tokens.py`, `tests/test_app_offline.py` |
| 2 | Identidade válida sem convite (ou sem e-mail verificado) não é admitida e não cria usuário | `tests/test_api_postgres.py` |
| 3 | Usuário A não lê, altera nem detecta o workspace de B | `test_a_user_cannot_see_or_change_another_users_workspace` |
| 4 | Papel/ID enviados pelo navegador são ignorados | `test_identity_and_role_sent_by_the_browser_are_ignored` |
| 5 | Criação repetida ou concorrente gera um único workspace | testes de criação (PostgreSQL) |
| 6 | Nome e fuso inválidos e campos não permitidos são recusados sem eco | `test_app_offline.py`, `test_api_postgres.py` |
| 7 | Atualização com versão obsoleta é recusada e nada muda; concorrentes: uma vence | testes de atualização (PostgreSQL) |
| 8 | Status e capabilities só informam o que é real | testes de status/capabilities + `dashboard` no web |
| 9 | Respostas autenticadas não são cacheáveis; cliente sem cache | `test_authenticated_responses_are_never_cacheable`, `client.test.ts` |
| 10 | Contrato frontend/backend em sincronia | `openapi.json` e `schema.d.ts` conferidos; `contract.test.ts` |
| 11 | Estados de loading, erro, vazio, não admitido e conflito na interface | `screens.test.tsx` |
| 12 | Migrations criam o mesmo schema dos modelos e fazem round-trip | `test_migrations_match_the_models_and_round_trip` |
| 13 | **Fluxo real com Clerk real** | **não verificado** — ver abaixo |

## Bloqueios e o que depende de configuração externa

1. **Forma real do token do Clerk não validada.** A API exige as claims `email` e
   `email_verified` (booleano `true`), além de `azp`. O token de sessão padrão do Clerk
   **não inclui e-mail**: é preciso personalizá-lo no painel (Sessions → Customize session token),
   por exemplo `{"email": "{{user.primary_email_address}}", "email_verified": "{{user.email_verified}}"}`.
   Os nomes exatos dos shortcodes e o tipo do valor (booleano × texto) **devem ser confirmados** no
   painel; se vierem como texto, a admissão falhará fechada (`EMAIL_NOT_VERIFIED`), nunca aberta.
2. **Instância Clerk.** Não foi provisionada. Para convite privado, restrinja os cadastros no
   painel (Restricted); mesmo sem isso a API só admite e-mails convidados.
3. **PostgreSQL gerenciado.** Não foi provisionado (Railway/outro); local e CI usam contêineres
   descartáveis. Nenhuma migration foi aplicada em banco real.
4. **Deploy.** Não feito (Vercel/Railway dependem dos itens acima e de autorização).
5. **Sem teste em navegador real.** A interface foi exercitada com Testing Library (jsdom) e
   `next build`; não houve execução com navegador nem avaliação visual do tema.
6. **Sem CSP** nos cabeçalhos do web (Clerk exige uma política cuidadosa); só `nosniff`,
   `X-Frame-Options`, `Referrer-Policy` e `Cache-Control`.
7. **ESLint não configurado** no `apps/web`; a checagem é `tsc` estrito + testes.
8. **JWKS por instância** (cache em memória do processo): com várias réplicas cada uma busca o seu.

## Próximos incrementos recomendados

1. Validar o fluxo real com Clerk de desenvolvimento (corrige o item 1) e registrar a forma real do token.
2. Provisionar PostgreSQL de homologação e a pipeline de migrations (com autorização).
3. Teste de ponta a ponta em navegador (Playwright) contra API e banco descartáveis.
4. S3 — cofre de segredos, antes de qualquer credencial real.
5. Heartbeat e supervisor de workers (S8) para que o status possa, enfim, mostrar algo operacional.
