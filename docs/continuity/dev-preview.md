# Checkpoint — primeiro deploy de desenvolvimento (14/11/2026)

Estado: auditoria concluída; **deploy não executado**, aguardando decisão do usuário sobre
conflito entre Preview obrigatório e primeiro deployment Vercel classificado como Production.
Não confundir este checkpoint com autorização para executar essa exceção.

## Git e publicação

- Branch desta auditoria: `codex/dev-preview-readiness`, no worktree
  `F:\projetos\promo_bot_saas`. Reutilizar essa branch ao continuar esta tarefa.
- Base/fonte de um futuro deploy: PR #10 aberto, `feat/saas-web-foundation`,
  `a98b94224f2bbecce95fae5c1f15d420988cab00`. SHA informado = local = remoto = GitHub.
- `origin/main`: `cde93c3cb4381481e50c57fd3180207f5c93c01f`, após fetch.
- PR #9 e sua branch preservados em `bf8e2f15f54ce66d0b63f6e5b4bcca9a7a587108`.
- Último commit desta tarefa confirmado **local e remotamente antes de salvar este checkpoint**:
  `7adc2a192530c49daa8fd03d401e545bb696f387` (`docs: audit isolated development preview readiness`).
  `git push -u origin codex/dev-preview-readiness` concluído; upstream configurado.
- [Diff exclusivo da auditoria](https://github.com/Lipe-meira/promo_bot/compare/feat/saas-web-foundation...codex/dev-preview-readiness).
  O commit que inclui este checkpoint é posterior: consultar `git rev-parse HEAD` e
  `git ls-remote origin refs/heads/codex/dev-preview-readiness` para a entrega atual.
- Nenhuma alteração de código, merge ou modificação dos PRs #9/#10. Checkout principal e seus
  exemplos locais modificados foram preservados. Nenhum commit de terceiros foi incluído.

## Resultado e evidências

O [relatório de auditoria](../DEV_PREVIEW_AUDIT.md) contém variáveis, configuração proposta
(`apps/web`, Next.js, Node 22.x, `npm ci`, `npm run build`), custos consultados e roteiro da API.

- Vercel conectada; consultas por nome `promo` e URL do repositório na equipe conectada vazias.
  Não foram criados projetos, deployments, integrações Git, variáveis ou recursos pagos.
  Nenhum deploy automático de main foi habilitado por esta tarefa.
- Bloqueio confirmado na documentação Vercel: primeiro deployment é Production mesmo com
  `--target=preview`. Não tentar contornar com um deployment que falhe/cancele: também seria
  Production e continua fora da autorização atual.
- Clerk Development não configurado nas fontes locais consultadas; backend e PostgreSQL não
  provisionados nesta tarefa. Plano/cobrança efetivos da conta não confirmados pelo conector.
- Web revalidado: contrato API, TypeScript, 138 testes/7 arquivos e build sintético aprovados;
  `npm ls --depth=0` aprovado. Zero padrões de segredo verificados em `.next/static`, limitado
  aos artefatos sintéticos. CI do PR #10: `api` e `web` aprovados, conferidos no GitHub.
- Links locais, coerência com contratos, ignore e diff revisados para a entrega documental.
- Não executados: testes Python novos (nenhum código Python alterado), PostgreSQL, migrations,
  browser/live, login real, criação de workspace real, logs remotos ou qualquer operação do bot.

## Próxima ação

Apresentar as opções ao usuário antes de qualquer criação externa: manter a restrição absoluta
e resolver com a Vercel um Preview inicial suportado; ou obter autorização explícita para a
classificação Production somente no primeiro deploy de um projeto novo dedicado ao dev, sem
credenciais produtivas e sem conexão Git automática. Não reutilizar outros projetos sem nova
autorização específica. Confirmar plano/cotas antes de criar recursos; não ativar planos pagos.

Depois: configurar Clerk Development diretamente no serviço (sem segredos no chat), resolver
as claims assinadas e a semântica de e-mail primário verificado; autorizar separadamente API e
banco; migrations antes do tráfego; `/readyz`, CORS/azp exatos e admissão do primeiro usuário.
Só então validar o SaaS completo. Se houver somente login web, não declarar dashboard/workspaces
funcionais. Telegram e AliExpress permanecem fora do escopo.
