# Primeiro deployment protegido — 16/11/2026

Estado: **READY**, frontend publicado exclusivamente no projeto de desenvolvimento autorizado.
A exceção do primeiro deployment classificado como Production foi utilizada uma vez.
Os próximos deployments devem ser **Preview**, mantendo All Deployments.

## Identificação

| Item | Resultado |
| --- | --- |
| Projeto | `promo-bot-dev`, `prj_j0PrKiqCzcB8cUIzTfbuxwQDTNrV` |
| Equipe | `lipe-s-projects1`, `team_IdIH9Z57lKO3PxfIz8sDJFqN` |
| Plano | Hobby, reconfirmado no painel; nenhum upgrade/adicional contratado |
| URL do deployment | [Abrir login protegido](https://promo-bot-pktag8w18-lipe-s-projects1.vercel.app/sign-in) |
| Deployment | `dpl_8FDdRzwJLnW52g2RAZFvY7GanMbL` |
| Inspeção | [Deployment na Vercel](https://vercel.com/lipe-s-projects1/promo-bot-dev/8FDdRzwJLnW52g2RAZFvY7GanMbL) |
| Fonte | PR #10, `feat/saas-web-foundation` |
| SHA implantado | `a98b94224f2bbecce95fae5c1f15d420988cab00` |
| Classificação | Production, somente pela exceção expressa para o projeto dev |
| Build | `npm ci` + `npm run build`, aprovado; Vercel registrou 33 segundos |

## Preparação e origem comprovada

- `git fetch origin` e GitHub confirmaram o HEAD do PR #10 no SHA aprovado. Nenhum código de
  `apps/web`/`apps/api` divergia desse SHA no checkout documental. PR #9 e main preservados.
- O payload foi extraído com `git show <SHA>:<arquivo>`, diretamente dos 46 blobs versionados em
  `apps/web` (297.853 bytes), sem arquivos locais, `.env.local`, build anterior, credenciais ou
  código da branch documental. `.env.example` é apenas o template versionado, não é configuração
  de execução. Nenhum valor sintético foi fornecido ao deployment.
- Manifesto local dos caminhos e SHA-256 dos blobs, na ordem do Git:
  `f02fc705c5e327ac7e04b624cb05132274db088de220c228ed6f3ec96c2bc1f6`.
- Upload direto de arquivos pela API Vercel; sem `gitSource`, sem conectar GitHub. Metadados da
  Vercel confirmaram `githubCommitSha` no SHA acima e `githubCommitRef=feat/saas-web-foundation`.
  Esses metadados registram a origem informada; a extração dos blobs é a evidência dos bytes.
- Projeto/framework/raiz explicitados: Next.js, `apps/web`; Node configurado em 22.x, output
  padrão e `skipGitConnectDuringLink=true`. Não houve promoção posterior nem associação manual
  de domínio. Utilizar a URL única acima, sem supor que o domínio estável sirva esta build.

## Variáveis verificadas sem revelar valores

Consulta de metadados com `decrypt=false`; nenhum segredo recuperado ou registrado.

| Nome | Ambientes | Tipo Vercel |
| --- | --- | --- |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Production + Preview | encrypted |
| `CLERK_SECRET_KEY` | Production + Preview | sensitive |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | Production + Preview | encrypted |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Production + Preview | encrypted |

As chaves Development foram configuradas pelo usuário. O navegador carregou o Clerk real com
o aviso e o indicador **Development mode**, sem utilização de instância sintética.
`NEXT_PUBLIC_API_URL` está ausente. A análise do código mostrou que ela só é lida ao montar o
`SessionProvider` autenticado; a página pública `/sign-in` não depende dele. O build remoto e
o carregamento real da tela de login confirmaram que essa ausência não bloqueia esses dois
passos. Não foi definido localhost nem endpoint falso para contornar a ausência da API.

## Proteção e smoke test

| Verificação executada | Resultado |
| --- | --- |
| Proteção antes e depois do deploy | Vercel Authentication habilitada, `deploymentType=all`; painel mostrou Require Log In marcado e All Deployments salvo |
| GET anônimo para `/` e `/sign-in`, sem cookies/credenciais | HTTP 302 para `vercel.com`, com barreira de autenticação; aplicação não servida |
| Navegador autorizado na Vercel, `/sign-in` | Formulário real “Sign in to Promo Bot”, campo de e-mail e Continue, Clerk Development |
| Navegar para `/`, sem sessão Clerk | Redireciona ao formulário `/sign-in` |
| Console do navegador no smoke test | Nenhum erro observado; aviso esperado de chaves Clerk Development |
| Logs de runtime, filtro error/fatal, deployment exato, janela de 15 minutos | Nenhum registro encontrado; resultado limitado à janela e aos acessos feitos |
| Build | READY; TypeScript, geração das rotas e outputs concluídos |
| Padrões de segredo nos logs de build consultados | Nenhuma correspondência para secret key Clerk ou chave privada; não é auditoria integral dos bundles |
| Configuração Git após deployment | Painel continua declarando projeto sem repositório conectado e sem Deploy Hooks |
| Quantidade de deployments ao concluir o smoke test | Um, READY, no projeto autorizado |

Não foram criados shareable links, automation bypass secrets ou exceções públicas. O teste
autorizado usou a sessão normal da Vercel, sem ferramenta de fetch que gere bypass temporário.
Nenhum projeto preexistente, domínio personalizado, backend, banco ou plano pago foi alterado/criado.

## O que ainda não funciona ou não foi comprovado

- **Autenticação Clerk completa:** não houve submissão de e-mail, envio de código, criação de
  usuário ou conclusão de login. Está comprovado o carregamento do formulário real, não o fluxo
  completo de autenticação, logout ou troca de sessão.
- **Dashboard, onboarding/workspace e configurações:** indisponíveis sem FastAPI, PostgreSQL e
  uma `NEXT_PUBLIC_API_URL` real. O código lança erro ao montar a sessão autenticada sem essa
  variável. Fazer login não habilita o SaaS completo; não preencher endpoint fictício.
- A integração backend requer autorização separada, banco dev, migrations antes do tráfego,
  `/healthz`/`/readyz`, claims assinadas de e-mail primário verificado, CORS/azp exatos e admissão
  do primeiro usuário. Ver o [contrato SaaS](SAAS_WEB_FOUNDATION.md).
- Telegram, AliExpress e qualquer operação real do bot não foram executados.
- Avisos não fatais do build: range amplo `engines.node >=20.9.0` (alerta de atualização de major),
  configuração npm `cache-dir` e allowScripts do postinstall de core-js. Nenhuma dependência ou
  configuração de segurança foi alterada para silenciá-los. Fixar a série Node no código pode
  ser avaliado em tarefa futura; esta entrega preservou o SHA aprovado.

## Continuidade

Alterações desta retomada são exclusivamente documentais na branch `codex/dev-preview-readiness`.
As verificações aplicáveis são links locais, coerência com contratos, ignore e `git diff --check`.
Testes Python, banco e suíte local do web não foram repetidos: nenhum código mudou e o build
remoto e smoke test acima são as evidências novas. Não confundir os 138 testes registrados em
14/11 com execução nesta data. Retomar pelo [checkpoint](continuity/dev-preview.md).
