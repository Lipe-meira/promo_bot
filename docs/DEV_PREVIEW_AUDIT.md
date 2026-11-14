# Primeiro deploy de desenvolvimento — auditoria de 14/11/2026

Estado: **deploy interrompido antes da criação de recursos externos**. Não há URL Preview nem SHA
implantado. O usuário autorizou somente Preview, sem produção, cobrança nova, merge ou operações
do bot. A configuração abaixo é uma proposta revisável, não uma configuração já aplicada.

## Fonte e rastreabilidade

- Repositório: `https://github.com/Lipe-meira/promo_bot`.
- PR #10 aberto: `feat/saas-web-foundation`, HEAD local/remoto/GitHub
  `a98b94224f2bbecce95fae5c1f15d420988cab00`, igual ao HEAD auditado informado.
- Base atual após `git fetch origin`: `origin/main` em
  `cde93c3cb4381481e50c57fd3180207f5c93c01f`.
- PR #9 aberto e preservado: `codex/workspace-runtime-spec`, HEAD
  `bf8e2f15f54ce66d0b63f6e5b4bcca9a7a587108`.
- Auditoria documental: `codex/dev-preview-readiness`, criada do HEAD do PR #10 no worktree
  limpo `F:\projetos\promo_bot_saas`. Nenhum código do PR foi alterado. Para um futuro deploy,
  selecionar explicitamente o SHA do PR #10, revalidando-o se houver novos commits.
- Checkout principal em `codex/runtime-migration-safety`, com alterações preexistentes em
  `.env.example` e `config.example.yaml`: preservado, fora desta entrega.

## Bloqueios que exigem decisão

1. **Primeiro deploy Vercel classificado como Production.** A
   [documentação oficial](https://vercel.com/docs/deployments/environments#first-deployment)
   informa que um projeto novo usa Production no primeiro deploy, inclusive com
   `--target=preview` e branch diferente de main; usa também as variáveis de Production.
   A conexão Vercel funcionou. As consultas por nome `promo` e pelo URL do repositório na equipe
   conectada não retornaram projetos. Não houve tentativa de criar projeto/deployment para
   testar exceções na API. Um primeiro deploy falho/cancelado também conta como Production;
   não usar esse expediente sem autorização para a exceção.
2. **Clerk Development não configurado nas fontes consultadas.** O checkout web contém só o
   exemplo de ambiente; não há variáveis Clerk/API/Vercel no ambiente do processo. Não foram
   procuradas credenciais em `.private/` nem lidos arquivos secretos. Todas as páginas úteis
   exigem Clerk; `/` redireciona ao dashboard, protegido pelo proxy. Valores sintéticos
   compilam, mas não oferecem login real nem acesso às telas protegidas.
3. **Backend e PostgreSQL não provisionados nesta tarefa.** Login sozinho não permite criar
   workspace ou acessar dashboard/configurações funcionais: essas telas consultam `/v1/me`
   e as rotas de workspace. O exemplo `http://localhost:8000` não serve como backend online.
4. **Plano e cobrança efetivos da conta não confirmados.** As respostas das ferramentas de
   equipes/projetos não incluíram o plano. Não inferir gratuidade pela autenticação funcionar.
   Confirmar plano, cotas e controle de gastos antes de criar qualquer recurso.

Opções de retomada: manter a restrição absoluta e esclarecer com a Vercel um caminho suportado
para Preview inicial (ou indicar um projeto exclusivamente de desenvolvimento já inicializado,
com autorização específica para utilizá-lo); ou autorizar explicitamente a exceção de um primeiro
deployment tecnicamente Production em projeto novo dedicado ao desenvolvimento, com dados e
chaves de desenvolvimento, sem conexão Git automática. Nenhuma opção foi executada. Uma exceção
de classificação não autoriza custo, uso de recursos produtivos ou operações do bot.

## Configuração Vercel proposta

| Item | Configuração |
| --- | --- |
| Projeto | novo e exclusivo do desenvolvimento; nome proposto `promo-bot-dev-web` |
| Fonte | PR #10, SHA completo acima; nunca main ou o merge sintético do PR |
| Root Directory | `apps/web` |
| Framework | Next.js |
| Node | 22.x (mesma série do CI; teste local desta auditoria: 24.21.0) |
| Instalação | `npm ci`, usando `apps/web/package-lock.json` |
| Build | `npm run build` |
| Output Directory | padrão do framework (`.next`), sem exportação estática |
| Destino | Preview explícito, somente depois de resolver o bloqueio do primeiro deploy |
| Integração Git | não conectar inicialmente; deploy manual do SHA, sem gatilhos main |
| Proteção | manter Vercel Authentication; nenhum domínio personalizado necessário |

Não há `vercel.json`, configuração Railway ou Dockerfile de deploy nessa entrega. A raiz do
monorepo é do bot Python: não selecioná-la como aplicação Next.js. `rootDirectory` é configuração
do projeto Vercel. O workflow `saas-web.yml` só executa verificações; não tem etapa de deploy.
Nenhuma integração Git, variável remota, webhook ou automação foi criada/alterada nesta auditoria.
Assim, esta tarefa não habilitou deploy automático de main.

### Variáveis web

| Nome | Visibilidade / valor a configurar |
| --- | --- |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | pública; chave `pk_test_` da instância Development |
| `CLERK_SECRET_KEY` | secreta e exclusiva do servidor; `sk_test_` da mesma instância |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | pública; `/sign-in` |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | pública; `/sign-up` |
| `NEXT_PUBLIC_API_URL` | pública; origem HTTPS da FastAPI de desenvolvimento, sem barra final |

Configurar os valores reais diretamente no painel do serviço, sem colá-los em chat, comandos,
logs ou Git. Nunca prefixar um segredo com `NEXT_PUBLIC_`. As variáveis públicas são incorporadas
no build: alterar a API ou a chave pública exige novo deployment. Não popular Production como
atalho para o primeiro deploy sem a exceção explícita descrita acima.

## Caminho para o SaaS completo (avaliado, não executado)

1. Criar/selecionar uma aplicação Clerk separada e usar somente Development. Configurar e-mail
   primário verificado, restringir cadastro e customizar o **token de sessão padrão** com
   `email` e `email_verified`, conforme o [contrato existente](SAAS_WEB_FOUNDATION.md#bloqueios-e-o-que-depende-de-configuração-externa).
   Preservar `sid`, `sub`, `iss` e `azp`; não trocar por template que omita `sid`. A assinatura
   RS256 é verificada pela API via JWKS. Confirmar, sem registrar JWT ou dados pessoais, que
   `email_verified` é booleano e comprova o e-mail primário usado na admissão. Essa semântica
   continua bloqueando a validação do fluxo real; não relaxar a autorização para contorná-la.
2. Após autorização específica para os serviços, criar FastAPI e PostgreSQL exclusivos de
   desenvolvimento. Railway é viável em princípio; não foi verificado numa conta Railway.
   Usar raiz `apps/api`, Python 3.12, `uv sync --locked --no-dev`; manter fonte, `alembic.ini`
   e `migrations/` na imagem. A instalação editável padrão mantém a localização esperada das
   migrations; uma imagem baseada só no wheel exige revisar esse empacotamento antes do deploy.
3. Configurar as variáveis abaixo, sem importar as configurações do bot. PostgreSQL Railway ou
   Neon Free são opções, ainda sem seleção/provisionamento. Confirmar conexão TLS do driver com
   o provedor escolhido; não copiar parâmetros de outro driver sem validar asyncpg.
4. Pré-deploy: `uv run --no-sync alembic upgrade head`, **apenas** no novo banco autorizado,
   antes do tráfego. A [etapa pré-deploy Railway](https://docs.railway.com/deployments/pre-deploy-command)
   precisa concluir com sucesso. Não executar migrations no startup de cada réplica.
5. Start proposto para o ambiente Linux Railway:
   `uv run --no-sync uvicorn promo_api.main:create --factory --host 0.0.0.0 --port "$PORT"`.
   Configurar [healthcheck Railway](https://docs.railway.com/deployments/healthchecks) em `/readyz`.
   `/healthz` só prova liveness; `/readyz` exige banco acessível e heads Alembic exatos.
   O healthcheck de deploy não substitui monitoramento contínuo.
6. Depois de obter a URL web, permitir sua origem HTTPS exata em authorized parties e CORS;
   não usar `*` nem aceitar todos os subdomínios `vercel.app`. A cada URL Preview diferente,
   revisar ambas as listas. O cliente usa Bearer e `credentials: omit`.
7. Convidar o primeiro usuário via `uv run --no-sync promo-api-admin invite <email-verificado>`
   no ambiente de desenvolvimento autorizado. É admissão local na API, não envio de convite
   por e-mail. Login Clerk sem esse registro recebe `NOT_ADMITTED`. Não registrar o e-mail.
8. Validar no navegador login/logout e troca de sessão; criar workspace, recarregar, editar
   nome/fuso e conferir persistência. Verificar console, rede, logs sanitizados e bundles públicos.

| Variável API | Escopo / requisito |
| --- | --- |
| `PROMO_API_DATABASE_URL` | secreta; `postgresql+asyncpg://…` só do banco dev |
| `PROMO_API_CLERK_ISSUER` | HTTPS da instância Clerk Development |
| `PROMO_API_CLERK_AUTHORIZED_PARTIES` | origens web exatas, separadas por vírgula |
| `PROMO_API_CORS_ALLOWED_ORIGINS` | mesmas origens; padrão já usa authorized parties |
| `PROMO_API_CLERK_JWKS_URL` | opcional; padrão `<issuer>/.well-known/jwks.json` |
| `PROMO_API_ENVIRONMENT` | `development`; não confundir com classificação do host |

Telegram, AliExpress, workers, heartbeat, relay e publicação permanecem indisponíveis por
contrato. Mesmo após o fluxo de workspaces funcionar, isso não será um bot operacional.

## Custos e limites consultados em 14/11/2026

Valores públicos em USD; não representam o plano efetivo nem crédito disponível do usuário.

| Serviço | Oferta pública / decisão necessária |
| --- | --- |
| [Vercel](https://vercel.com/pricing) | Hobby US$ 0; Pro a partir de US$ 20/mês, com consumo adicional. Confirmar elegibilidade, plano e cotas; nenhum upgrade autorizado. [Hobby](https://vercel.com/docs/plans/hobby): 100 GB de transferência, 1 milhão de requisições CDN, 4 CPU-h/mês e logs de runtime por 1 hora. |
| [Railway](https://railway.com/pricing) | Free: trial de 30 dias/US$ 5, depois US$ 1/mês em crédito; limites de 0,5 GB RAM e volume por serviço. Não garante API+banco permanentes grátis. Hobby: mínimo US$ 5/mês, inclui US$ 5 de uso; excedente é cobrado. API e PostgreSQL consomem recursos separadamente. Plano pago e serviços exigem decisão específica. |
| [Neon](https://neon.com/pricing) | Alternativa PostgreSQL Free: 100 CU-h/mês e 1 GB por projeto; até 2 CU. Não provisionado; verificar suspensão/retomada por inatividade e limites de conexões antes do teste. Nenhum upgrade autorizado. |
| [Clerk](https://clerk.com/pricing) | Hobby gratuito. [Development](https://clerk.com/docs/guides/development/managing-environments) limita a 100 usuários e separa dados das instâncias; suficiente para piloto privado. Não ativar recursos pagos de produção. |

## Evidências e limites desta auditoria

- `git fetch origin`, SHAs locais/remotos, `gh pr view 9/10`: conferidos.
- `gh pr checks 10`: jobs `api` e `web` passaram no GitHub para o HEAD auditado. Isso é
  evidência do CI, não uma execução local nova da API nesta tarefa.
- Reexecutados em `apps/web`, dependências já instaladas: `npm run check:api`,
  `npm run typecheck`, `npm test`: aprovados, **138 testes / 7 arquivos**.
- `npm ls --depth=0`: aprovado. `npm run build`: aprovado, 7 rotas + proxy, usando somente
  valores sintéticos de CI no processo filho e telemetria Next desativada. Nenhum arquivo de
  ambiente foi criado. Build otimizado local não é deploy de produção.
- Inspeção dos arquivos gerados em `.next/static`: zero correspondências para padrões de
  secret key Clerk, conexão PostgreSQL ou chave privada. É uma verificação limitada sobre
  artefatos sintéticos; não comprova segurança de um futuro ambiente remoto.
- `.env.local`, `.next`, `node_modules` e `.env` da API são ignorados. `.vercel/` ainda não é
  ignorado; se uma retomada usar CLI local, adicionar a regra em branch dedicada **antes**
  de vincular o projeto. Esta auditoria usou só leitura via conector.
- Não executados: criação/deploy externo, login real, navegador, logs remotos, workspaces reais,
  migrations, PostgreSQL, serviços do bot ou testes live/browser. Não há ambiente para validar
  esses itens. Testes Python/checagens da raiz omitidos: alterações apenas documentais e
  nenhum código Python alterado. A publicação documental exige links locais e diff limpos.

Retomar pelo [checkpoint do deploy](continuity/dev-preview.md) após a decisão sobre o primeiro
deployment Vercel e a configuração Clerk; preservar os checkpoints de implementação anteriores.
