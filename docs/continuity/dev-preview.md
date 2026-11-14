# Checkpoint — primeiro deploy de desenvolvimento (14/11/2026)

Estado após autorização controlada: **projeto Vercel criado e All Deployments confirmado;
nenhum deployment criado**, aguardando Clerk Development.

O usuário autorizou um único primeiro deployment tecnicamente Production, em projeto novo
exclusivo do dev, com Vercel Authentication + All Deployments salvos antes do deploy. Não pedir
essa autorização novamente. Ela continua condicionada a proteção, ausência de custos novos e
credenciais de desenvolvimento; não autoriza lançamento público, backend, banco ou operações do
bot. Deployments seguintes devem usar Preview e manter a proteção.

## Git e publicação

- Branch desta auditoria: `codex/dev-preview-readiness`, no worktree
  `F:\projetos\promo_bot_saas`. Reutilizar essa branch ao continuar esta tarefa.
- Base/fonte de um futuro deploy: PR #10 aberto, `feat/saas-web-foundation`,
  `a98b94224f2bbecce95fae5c1f15d420988cab00`. SHA informado = local = remoto = GitHub.
- `origin/main`: `cde93c3cb4381481e50c57fd3180207f5c93c01f`, após fetch.
- PR #9 e sua branch preservados em `bf8e2f15f54ce66d0b63f6e5b4bcca9a7a587108`.
- Último commit desta tarefa confirmado **local e remotamente antes de salvar este checkpoint**:
  `614305672e7185f19c32a34ff57f70560918a008` (`docs: record development deploy blockers and continuity`).
  Publicado; upstream configurado. Este registro é posterior a esse commit.
- [Diff exclusivo da auditoria](https://github.com/Lipe-meira/promo_bot/compare/feat/saas-web-foundation...codex/dev-preview-readiness).
  O commit que inclui este checkpoint é posterior: consultar `git rev-parse HEAD` e
  `git ls-remote origin refs/heads/codex/dev-preview-readiness` para a entrega atual.
- Nenhuma alteração de código, merge ou modificação dos PRs #9/#10. Checkout principal e seus
  exemplos locais modificados foram preservados. Nenhum commit de terceiros foi incluído.

## Resultado e evidências

O [relatório de auditoria](../DEV_PREVIEW_AUDIT.md) contém variáveis, configuração salva,
custos e roteiro da API. Não usar esta branch documental como fonte do deploy; o código aprovado
continua sendo exclusivamente o SHA do PR #10 acima.

- Equipe `lipe's projects` / `lipe-s-projects1`, ID `team_IdIH9Z57lKO3PxfIz8sDJFqN`;
  **Hobby ativo**, confirmado no Billing do painel, sem upgrade/adicional contratado.
- Projeto novo `promo-bot-dev`, ID `prj_j0PrKiqCzcB8cUIzTfbuxwQDTNrV`.
  [Painel](https://vercel.com/lipe-s-projects1/promo-bot-dev).
- Proteção configurada na criação e relida: `ssoProtection.enabled=true`, `deploymentType=all`.
  Painel: Require Log In marcado, All Deployments e Save desabilitado. Sem exceções públicas,
  shareable links ou automation bypass secret; OPTIONS bypass desligado. Fonte confiável padrão
  somente do próprio projeto, nenhuma adicional configurada.
- Build salvo e conferido: Next.js, `apps/web`, Node 22.x, `npm ci`, `npm run build`, output
  padrão. Máquina Basic, on-demand concurrent builds desligados. Não houve build remoto.
- Painel Git confirmou projeto sem repositório conectado e sem Deploy Hooks. Criação usou
  `skipGitConnectDuringLink=true`; autoassign de domínios personalizados desativado. Nenhum
  auto-deploy de main habilitado, nem alteração em projetos preexistentes.
- API: zero deployments; `live=false`, `latestDeployment=null`. Variáveis vazias,
  `hiddenProductionEnvCount=0`, consulta sem decrypt. A plataforma atribuiu apenas
  `promo-bot-dev.vercel.app`; nenhum domínio personalizado. Não é URL de app implantado.
- Clerk Development ausente nas fontes locais consultadas e no projeto novo; não foram
  procuradas credenciais privadas. Backend/PostgreSQL permanecem fora desta autorização.
- Validações de código da etapa anterior em 14/11: contrato API, TypeScript, 138 testes/7
  arquivos, build sintético e `npm ls --depth=0` aprovados. Não reexecutados nesta retomada,
  pois só houve configuração externa e documentação. Não tratá-los como build remoto.
- Retomada: Git/SHAs conferidos após fetch, configurações externas relidas via API/painel;
  links locais, contratos, ignore e diff revisados antes da publicação documental.
- Não executados: deployment, smoke test do SaaS, acesso anônimo à aplicação, login Clerk,
  logs da aplicação, workspace real, Python/PostgreSQL/migrations, testes live/browser do
  projeto ou operações do bot. Navegador usado somente para conferir o painel Vercel.

## Próxima ação

O usuário deve criar/selecionar uma aplicação Clerk exclusiva do dev, selecionar Development e
inserir diretamente no projeto Vercel as chaves reais da mesma instância:
`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (`pk_test_`) e `CLERK_SECRET_KEY` (`sk_test_`, somente servidor).
Configurar nos ambientes Vercel Production **e** Preview, com `/sign-in` e `/sign-up` nas
variáveis públicas de rota. Não solicitar segredos no chat nem rodar inicializador Clerk que
reescreva o código aprovado. Não iniciar deployment automaticamente ao configurar as variáveis.

Na retomada, confirmar novamente projeto/plano, All Deployments, zero deployments e SHA exato;
enviar snapshot do SHA aprovado sem conectar GitHub. A autorização de um primeiro deployment
ainda não foi utilizada. Não fazer retry Production automaticamente se uma tentativa falhar.

Clerk sozinho libera teste de login; o app autenticado exige uma `NEXT_PUBLIC_API_URL` real e
backend funcional. Sem isso dashboard/workspaces/configurações continuam bloqueados. Não usar
localhost ou URL inventada. API/banco exigem autorização separada; depois validar claims de
e-mail primário verificado, migrations antes do tráfego, `/readyz`, CORS/azp e admissão inicial.
Não declarar o SaaS completo funcional com apenas login web. Telegram/AliExpress fora do escopo.
