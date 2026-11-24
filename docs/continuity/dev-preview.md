# Checkpoint — primeiro deploy protegido (16/11/2026)

Estado: **primeiro deployment concluído, READY**. Frontend e formulário real Clerk Development
carregam sob proteção Vercel. Não declarar login completo ou SaaS completo funcional.

## Autorização e limites

O usuário autorizou um único primeiro deployment classificado como Production, somente em
projeto novo de desenvolvimento com Vercel Authentication + All Deployments ativo antes do
deploy. **Essa exceção foi utilizada. Todos os próximos deployments devem ser Preview.**
Não criar outro Production automaticamente, não conectar GitHub, não habilitar auto-deploy e
não remover a proteção. Backend, banco, planos pagos, merge e operações do bot continuam fora
da autorização atual. Nenhum segredo deve ser solicitado no chat.

## Git e publicação documental

- Repositório: `Lipe-meira/promo_bot`. Branch documental: `codex/dev-preview-readiness`, no
  worktree `F:\projetos\promo_bot_saas`. Reutilizar esta branch ao continuar a mesma tarefa.
- Código aprovado e efetivamente enviado: PR #10, `feat/saas-web-foundation`,
  `a98b94224f2bbecce95fae5c1f15d420988cab00`, confirmado após fetch e no GitHub. Essa branch
  documental não substitui a fonte aprovada para o deployment.
- Main preservada: `cde93c3cb4381481e50c57fd3180207f5c93c01f`; PR #9 preservado em
  `bf8e2f15f54ce66d0b63f6e5b4bcca9a7a587108`. Nenhum merge ou alteração de código.
- Último commit documental confirmado local/remotamente **antes de salvar este checkpoint**:
  `82ff772fa3ef3c5c5480ae7706d2991ca0d41a6b`. Upstream já configurado.
  O registro deste deployment está em commit posterior, consultável com `git rev-parse HEAD`
  e `git ls-remote origin refs/heads/codex/dev-preview-readiness`.
- [Diff da documentação](https://github.com/Lipe-meira/promo_bot/compare/feat/saas-web-foundation...codex/dev-preview-readiness).
  Checkout principal e alterações locais preexistentes preservados.

## Ambiente e deployment

- Projeto: `promo-bot-dev`, `prj_j0PrKiqCzcB8cUIzTfbuxwQDTNrV`.
- Equipe: `lipe-s-projects1`, `team_IdIH9Z57lKO3PxfIz8sDJFqN`; Hobby reconfirmado no painel.
  Nenhum upgrade/adicional contratado; nenhuma medição de fatura foi feita.
- Deployment: `dpl_8FDdRzwJLnW52g2RAZFvY7GanMbL`, READY, target Production excepcional.
- [URL protegida de login](https://promo-bot-pktag8w18-lipe-s-projects1.vercel.app/sign-in).
- [Inspeção Vercel](https://vercel.com/lipe-s-projects1/promo-bot-dev/8FDdRzwJLnW52g2RAZFvY7GanMbL).
- Upload direto dos 46 blobs de `apps/web` extraídos do SHA aprovado (297.853 bytes).
  Sem `gitSource`, sem integração Git, sem arquivos secretos ou payload da branch documental.
  Metadados Vercel confirmam o SHA e a branch de origem.
- Manifesto SHA-256 dos caminhos/blobs:
  `f02fc705c5e327ac7e04b624cb05132274db088de220c228ed6f3ec96c2bc1f6`.
- Configuração: Next.js, raiz `apps/web`, Node configurado 22.x, `npm ci`,
  `npm run build`, output padrão, `skipGitConnectDuringLink=true`. Build remoto passou em
  33 s; nenhum valor sintético fornecido. Sem promoção/domínio personalizado.
- Proteção relida antes/depois: `ssoProtection.enabled=true`, `deploymentType=all`.
  Sem shareable link, segredo de bypass ou exceção pública criada.
- Painel Git relido após o deploy: projeto sem repositório conectado e sem Deploy Hooks.
  Lista de deployments contém exatamente um READY.

## Variáveis (somente metadados, sem valores)

Confirmadas em Production e Preview com `decrypt=false`:

- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`: encrypted.
- `CLERK_SECRET_KEY`: sensitive.
- `NEXT_PUBLIC_CLERK_SIGN_IN_URL`: encrypted.
- `NEXT_PUBLIC_CLERK_SIGN_UP_URL`: encrypted.

`NEXT_PUBLIC_API_URL` ausente. Não bloqueou build remoto nem formulário de login.
É obrigatória na montagem da sessão autenticada e continua bloqueando as telas do SaaS.

## Validações novas e limitações

- GET anônimo sem cookies para `/` e `/sign-in`: 302 para autenticação em `vercel.com`;
  a aplicação não foi servida.
- Navegador autorizado na Vercel: formulário “Sign in to Promo Bot”, e-mail e Continue,
  indicador real Development mode; `/` redireciona ao login sem sessão Clerk.
- Console sem erros observados; aviso esperado de chaves Development. Consulta runtime
  error/fatal, janela 15 min, deployment exato: nenhum registro encontrado.
- Build READY: TypeScript e rotas concluídos. Avisos não fatais de engines Node amplo,
  cache-dir npm e allowScripts core-js, descritos no [relatório](../DEV_FIRST_DEPLOY.md).
- Padrões de segredo verificados nos logs de build: nenhuma correspondência. Não é auditoria
  integral dos bundles. Nenhum JWT, chave ou dado de usuário foi registrado.
- Não executados: submissão de e-mail/código, criação de usuário, conclusão de login/logout,
  troca de sessão, workspace/dashboard/configurações autenticadas, testes Python/PostgreSQL,
  migrations, suíte web local nova ou operações do bot.
- Sem alteração de código: validação documental por links, contratos, ignore e diff; não
  tratar os 138 testes de 14/11 como execução de 16/11.

## Próximos passos

O usuário pode abrir a URL e visualizar o login após autenticar-se na Vercel. Completar Clerk
não libera as telas autenticadas: sem API real o código lança erro ao ler `NEXT_PUBLIC_API_URL`.
Não preencher localhost nem URL fictícia e não criar backend/banco por iniciativa própria.

Para testar o SaaS completo, obter autorização específica para FastAPI/PostgreSQL dev; configurar
URL real, TLS, migrations antes do tráfego e healthchecks, CORS/azp exatos, claims assinadas de
e-mail primário verificado e admissão do primeiro usuário. Então publicar **Preview** com All
Deployments e validar persistência no navegador. Telegram e AliExpress permanecem fora do escopo.
