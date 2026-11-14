# Checkpoint — SaaS web, fundação (14/11/2026)

Objetivo: primeira entrega funcional do SaaS (login Clerk → workspace no PostgreSQL → dashboard →
configurações persistidas). Detalhes, endpoints, decisões e critérios de aceite:
[SAAS_WEB_FOUNDATION.md](../SAAS_WEB_FOUNDATION.md). **Independente da S1** (PR #9): este trabalho
não importa `promo_bot`, não depende do merge da S1 e não altera `docs/continuity/backend.md`.

- Repositório: `Lipe-meira/promo_bot`. Branch: `feat/saas-web-foundation` (reutilizar ao continuar),
  criada de `origin/main` em `cde93c3cb4381481e50c57fd3180207f5c93c01f`. Worktree:
  `F:\projetos\promo_bot_saas` (não usar o worktree da S1 nem o checkout principal).
- Commits de implementação (validados): API `e3743f49cb20eb2298c8671c92ef2a01ef164cb9`, web
  `5c1206ec131ff0b4290b7fb22179c8cdcba335eb`, CI `5d5c1dc98df931793487c7c542e98ba6d3dd6160`. Este
  checkpoint e o documento de arquitetura estão em commit posterior: consulte
  `git log -1 --format=%H -- docs/continuity/saas-web.md` e `git ls-remote origin refs/heads/feat/saas-web-foundation`.
- Diff: [main...feat/saas-web-foundation](https://github.com/Lipe-meira/promo_bot/compare/main...feat/saas-web-foundation).
  PR Draft: ver o link no relatório de entrega. Merge e deploy: **não autorizados**.

## Validações (executadas, offline/sintéticas)

| Verificação | Resultado |
| --- | --- |
| `apps/api`: `ruff format --check`, `ruff check`, `mypy` (strict), `uv lock --check`, `openapi --check` | limpos |
| `apps/api`: `pytest` (sem banco) | 73 passed |
| `apps/api`: `scripts/test-postgres.ps1` (`-m postgres`, contêiner descartável, removido ao fim) | 45 passed |
| `apps/web`: `npm run check:api`, `tsc --noEmit` | limpos |
| `apps/web`: `vitest run` | 49 passed; 12/12 execuções repetidas sob carga paralela |
| `apps/web`: `next build` (chaves Clerk sintéticas) | compilou (7 rotas + proxy) |
| Raiz (bot legado): `ruff format --check`, `ruff check`, `mypy src`, `uv lock --check --offline`, `git diff --check` | limpos |

CI (GitHub Actions, workflow `saas-web`, head `0a5635a5c6907f4affdf3c0edc777c404d19e31b`): jobs `api`
(inclui os 45 testes PostgreSQL em serviço descartável) e `web` (inclui `next build`) **success**,
nas execuções de push e de pull_request.

Não executados: suíte `pytest` do bot na raiz (nenhum arquivo do bot foi alterado; `apps/` não está
em `testpaths`), testes live/browser, navegador real, qualquer fluxo com Clerk real ou PostgreSQL real.

Mutações: 9 pontos do código de autorização/admissão da API (filtro de membership, versão, e-mail
verificado, usuário revogado, `azp`, `iss`, `no-store`, 404 indistinguível, unicidade do convite)
detectados; o lock explícito do convite é redundante com as constraints únicas (corrida coberta por
teste). 13 pontos do web (chave por usuário, `no-store`, credenciais, `Authorization`, 403, texto do
servidor, token em cache, versão, resposta/erro atrasados, alegação de online, heartbeat inventado,
recarga que apaga dados) detectados.

Incidentes de processo (corrigidos): `alembic.ini` gravado com BOM por `Set-Content` quebrou o
Alembic (a execução de base das mutações revelou; checagem de BOM em todos os arquivos feita);
duas corridas **nos testes** do web apareceram só sob carga e foram trocadas por asserções
determinísticas.

## Pendências e riscos (não declarar como feito)

1. **Forma real do token do Clerk não validada**: a API exige `email` e `email_verified` (booleano)
   além de `azp`; o token padrão do Clerk não traz e-mail. Confirmar os shortcodes no painel.
2. Sem instância Clerk, PostgreSQL gerenciado, deploy, CSP, ESLint nem teste em navegador (ver
   "Bloqueios" no documento de arquitetura).
3. Um workspace por dono e papel único `owner` são decisões reversíveis de MVP.

## Próximos passos

Validar com um Clerk de desenvolvimento e registrar o formato real do token; Playwright contra
API+banco descartáveis; só depois homologação/deploy (com autorização). Cofre (S3) antes de
qualquer credencial real.
