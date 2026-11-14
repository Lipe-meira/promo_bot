# Primeiro deploy de desenvolvimento — auditoria de 14/11/2026

Estado atualizado após a autorização controlada de 14/11/2026: **projeto Vercel criado e
protegido; nenhum deployment criado**. O usuário autorizou um único primeiro deployment
classificado como Production, exclusivamente em projeto novo de desenvolvimento e com Vercel
Authentication + All Deployments salvos antes do deploy. A execução parou pela ausência de
Clerk Development. Backend, banco, planos pagos, merge e operações do bot continuam proibidos.

## Projeto criado e verificações externas

- Projeto: `promo-bot-dev`, ID `prj_j0PrKiqCzcB8cUIzTfbuxwQDTNrV`.
- Equipe: `lipe's projects`, slug `lipe-s-projects1`, ID `team_IdIH9Z57lKO3PxfIz8sDJFqN`.
  Plano **Hobby ativo**, confirmado no painel Billing da equipe. Nenhum upgrade ou adicional pago.
- [Painel do projeto](https://vercel.com/lipe-s-projects1/promo-bot-dev).
- Vercel Authentication + **All Deployments** configurados na própria criação. Leitura posterior
  da API confirmou `ssoProtection.enabled=true`, `deploymentType=all`. O painel mostrou
  Require Log In marcado, All Deployments selecionado e Save desabilitado (sem alteração pendente).
  [Proteção salva](https://vercel.com/lipe-s-projects1/promo-bot-dev/settings/deployment-protection).
- Sem segredo de automation bypass, links compartilháveis ou exceções públicas; OPTIONS bypass
  desligado. O painel mostrou apenas a fonte confiável padrão do próprio projeto, sem fonte
  adicional configurada. Não foram criadas credenciais de bypass.
- Configuração de build conferida no painel: Next.js, raiz `apps/web`, Node 22.x, `npm ci`,
  `npm run build`, output padrão. Máquina Basic e on-demand concurrent builds desativados.
- [Configuração Git](https://vercel.com/lipe-s-projects1/promo-bot-dev/settings/git) mostrou
  explicitamente que o projeto não está conectado a um repositório e não pode criar Deploy Hooks
  sem conexão. Não houve integração GitHub nem auto-deploy de main.
- API de deployments: lista vazia, `live=false`, `latestDeployment=null`.
- API de variáveis: lista vazia, inclusive `hiddenProductionEnvCount=0`; consulta sem decrypt.
- Domínios: somente `promo-bot-dev.vercel.app`, atribuído automaticamente pela plataforma;
  nenhum domínio personalizado. Esse nome **não é URL de uma aplicação implantada**.
  Nenhum teste de acesso anônimo à aplicação foi possível sem deployment.
- A classificação Production excepcional ainda **não foi utilizada**. Não gastar a autorização
  em build sintético ou tentativa inútil. Após o primeiro deployment, todos os novos devem ser
  Preview, mantendo All Deployments e sem conectar Git automaticamente.

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

## Bloqueios e condições atuais

1. **Primeiro deploy Vercel classificado como Production: exceção limitada autorizada.** A
   [documentação oficial](https://vercel.com/docs/deployments/environments#first-deployment)
   informa que um projeto novo usa Production no primeiro deploy, inclusive com
   `--target=preview` e branch diferente de main; usa também as variáveis de Production.
   O projeto exclusivo acima e a proteção foram preparados. Nenhum deployment foi criado.
2. **Clerk Development não configurado nas fontes consultadas.** O checkout web contém só o
   exemplo de ambiente; não há variáveis Clerk/API/Vercel no ambiente do processo. Não foram
   procuradas credenciais em `.private/` nem lidos arquivos secretos. Todas as páginas úteis
   exigem Clerk; `/` redireciona ao dashboard, protegido pelo proxy. Valores sintéticos
   compilam, mas não oferecem login real nem acesso às telas protegidas.
3. **Backend e PostgreSQL não provisionados nesta tarefa.** Login sozinho não permite criar
   workspace ou acessar dashboard/configurações funcionais: essas telas consultam `/v1/me`
   e as rotas de workspace. O exemplo `http://localhost:8000` não serve como backend online.
4. **Plano confirmado, sem contratação.** Hobby ativo na equipe. Vercel Authentication com
   All Deployments não exige adicional pago, conforme a
   [documentação atual](https://vercel.com/docs/deployment-protection#all-deployments) e o
   [anúncio oficial](https://vercel.com/changelog/protect-production-deployments-for-free-on-every-plan).
   Criação do projeto sem build não consumiu um deployment. Não foi feita medição de fatura;
   não apresentar ausência de contratação como uma auditoria financeira da conta.

Retomada: configurar Clerk Development no projeto já criado, sem solicitar novamente a exceção
que o usuário já autorizou. Não publicar enquanto faltar a integração real necessária ao login.
Uma exceção de classificação não autoriza custo ou recursos produtivos.

## Configuração Vercel salva

| Item | Configuração |
| --- | --- |
| Projeto | novo e exclusivo do desenvolvimento: `promo-bot-dev` |
| Fonte | PR #10, SHA completo acima; nunca main ou o merge sintético do PR |
| Root Directory | `apps/web` |
| Framework | Next.js |
| Node | 22.x (mesma série do CI; teste local desta auditoria: 24.21.0) |
| Instalação | `npm ci`, usando `apps/web/package-lock.json` |
| Build | `npm run build` |
| Output Directory | padrão do framework (`.next`), sem exportação estática |
| Destino | primeiro deployment Production autorizado sob as condições acima; seguintes Preview |
| Integração Git | não conectar inicialmente; deploy manual do SHA, sem gatilhos main |
| Proteção | manter Vercel Authentication; nenhum domínio personalizado necessário |

Não há `vercel.json`, configuração Railway ou Dockerfile de deploy nessa entrega. A raiz do
monorepo é do bot Python: não selecioná-la como aplicação Next.js. `rootDirectory` é configuração
do projeto Vercel. O workflow `saas-web.yml` só executa verificações; não tem etapa de deploy.
Nenhuma integração Git, variável remota, webhook ou automação foi criada/alterada nesta tarefa.
O projeto foi criado com `skipGitConnectDuringLink=true`; autoassign de domínios personalizados
também foi desativado. Nenhum projeto preexistente foi alterado.

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
no build: alterar a API ou a chave pública exige novo deployment. Neste projeto isolado, os
valores para o primeiro deployment devem estar no ambiente Vercel **Production**, sempre com
chaves Clerk **Development**. Configurá-los também em Preview para os deployments seguintes.

### Participação necessária: Clerk Development

1. No [Clerk Dashboard](https://dashboard.clerk.com/), criar/selecionar uma aplicação exclusiva
   do desenvolvimento e selecionar a instância **Development**. Não ativar Production.
2. Em API Keys, obter a publishable key `pk_test_` e a secret key `sk_test_` da mesma instância.
   A integração Next.js já existe no SHA aprovado: não rodar um inicializador que reescreva o app.
3. No projeto `promo-bot-dev`, abrir **Settings → Environments → Production → Environment
   Variables** (ou a seção Environment Variables do painel). Inserir diretamente as duas chaves
   com os nomes da tabela, sem expor a secret key no chat ou no frontend. Selecionar também
   Preview para reutilizar exclusivamente essas chaves de desenvolvimento nos próximos testes.
4. Definir `NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in` e
   `NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up` nos mesmos ambientes. Não iniciar deploy no painel.
5. Informar apenas que a configuração foi concluída. Na retomada, revalidar plano, proteção,
   zero deployments e SHA; publicar somente o código aprovado, por upload do snapshot exato,
   sem conectar GitHub. Não usar a branch documental como código de deploy.

Com Clerk configurado, o teste inicial pode comprovar somente a tela de login e autenticação.
O código aprovado exige `NEXT_PUBLIC_API_URL` ao montar a sessão autenticada; sem uma URL real
de API dev, dashboard/onboarding/configurações não funcionam. Não preencher com localhost ou
URL inventada e não criar backend/banco sem autorização específica. Claims de e-mail, CORS e
convite local são condições adicionais para o fluxo completo descrito abaixo.

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

Valores públicos em USD. A equipe Vercel foi confirmada em Hobby; demais serviços não foram
provisionados e seus valores não representam créditos disponíveis do usuário.

| Serviço | Oferta pública / decisão necessária |
| --- | --- |
| [Vercel](https://vercel.com/pricing) | Equipe em Hobby ativo (US$ 0); All Deployments via Vercel Authentication sem adicional. Nenhum upgrade contratado. [Hobby](https://vercel.com/docs/plans/hobby): 100 GB de transferência, 1 milhão de requisições CDN, 4 CPU-h/mês e logs de runtime por 1 hora. |
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
  de vincular o projeto. A criação/configuração desta retomada usou o conector, sem CLI local.
- A criação externa **somente do projeto Vercel** e sua configuração estão confirmadas acima.
  Navegador usado para conferir Billing, proteção, Git e build do painel Vercel, sem executar o
  SaaS. Nenhuma credencial lida no navegador.
- Build e 138 testes são da etapa anterior desta mesma tarefa, em 14/11/2026; não foram
  reexecutados após a configuração externa (nenhum código mudou).
- Não executados nesta retomada: deployment/build remoto, login Clerk real, acesso anônimo ao
  SaaS, logs remotos da aplicação, workspaces reais, migrations, PostgreSQL, operações do bot ou
  testes live/browser do projeto. Testes Python/checagens da raiz omitidos: alterações apenas
  documentais, sem código Python alterado. Validar links locais, ignore e diff antes do push.

Retomar pelo [checkpoint do deploy](continuity/dev-preview.md) após configurar Clerk Development;
preservar os checkpoints de implementação anteriores.
