# Checkpoint — SaaS web, fundação (14/11/2026; correção W-01/W-02 no mesmo dia)

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
| `apps/api`: `pytest` (sem banco) | 73 passed (inalterado) |
| `apps/api`: `scripts/test-postgres.ps1` (`-m postgres`, contêiner descartável, removido ao fim) | 57 passed (45 + 12 de readiness) |
| `apps/web`: `npm run check:api`, `tsc --noEmit` | limpos |
| `apps/web`: `vitest run` | 72 passed (49 + 23 de W-01); 6/6 execuções em paralelo sob carga |
| `apps/web`: `next build` (chaves Clerk sintéticas) | compilou (7 rotas + proxy) |
| Raiz (bot legado): `ruff format --check`, `ruff check`, `mypy src`, `uv lock --check --offline`, `git diff --check` | limpos |

CI da entrega original (GitHub Actions, workflow `saas-web`, head `0a5635a5c6907f4affdf3c0edc777c404d19e31b`; o CI da correção W-01/W-02 é verificado depois do push — ver relatório e `gh pr checks 10`): jobs `api`
(inclui os testes PostgreSQL em serviço descartável) e `web` (inclui `next build`) **success**,
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

## Correção dos bloqueadores da auditoria independente (PR #10, SHA auditado `cc4b06ef…`)

Auditoria (Codex): S1/PR #9 GO WITH NOTES; PR #10 NO-GO por dois defeitos P2 reproduzidos.

**W-01 — corrida entre sessão e operação pendente.** Causa raiz: o cliente HTTP era criado uma vez por
provedor com `() => getTokenRef.current()`; o `getToken()` do Clerk responde pela sessão ativa *quando
resolve*, então o envio iniciado por A (dados de A) saía com o token de B, e a API, corretamente,
autorizava o token recebido. Correção (`apps/web/src/lib/api/client.ts`, `token.ts`, `lib/session.tsx`):
`ApiSession` (`userId` + `signal()`), `AbortController` por instância do provedor criado em
`useLayoutEffect` (recriado no remount do StrictMode) e abortado na desmontagem; a operação captura o
sinal ao iniciar e, após o `getToken()`, recusa enviar se a sessão acabou ou se o `sub` do token não é o
`userId` (`SESSION_CHANGED`; token ilegível → `AUTH_INVALID_TOKEN`); o sinal vai ao `fetch` e respostas
tardias não são entregues. Antes de montar o provedor (layout effect de filho) falha fechado.
Evidência RED: 8 dos 9 testes de integração iniciais falharam no código antigo (POST de A com token de
B; POST após logout; POST de provedor desmontado; PATCH de A sob B; sem `signal` na requisição em voo),
mais 23 de 26 do cliente por assinatura ausente; GREEN: 72/72. Limite: abortar requisição já enviada
não desfaz o efeito no servidor.

**W-02 — readiness aceitava migrations incompatíveis.** Causa raiz: `SELECT 1 FROM alembic_version` só
prova que a tabela existe. Correção (`apps/api/src/promo_api/readiness.py`, `app.py`): o conjunto de
heads é lido na subida dos scripts versionados (`ScriptDirectory`; sem constante; sem migrations legíveis
o processo não sobe) e `/readyz` só dá 200 se `alembic_version` for exatamente esse conjunto; qualquer
outro caso (vazio, ausente, desconhecido, anterior, posterior, linha extra, vários heads incompletos,
banco indisponível) é 503 `NOT_READY` genérico, motivo só no log. Não migra. Evidência RED: 4 de 8 testes
falharam (200 indevido: vazio, desconhecido, linha extra, downgrade) e 4 por `migrations_dir` ausente;
tabela ausente e banco indisponível já davam 503 (caracterização). GREEN: 12/12 em PostgreSQL descartável,
incluindo downgrade→upgrade com as migrations reais.

**Mutações (código novo):** W-01 11/11 detectadas (sem checagem antes/depois do token, sem comparar
`sub`, token ilegível aceito, sinal fora do `fetch`, aborto como erro de rede, resposta tardia entregue,
sem abort no unmount, provedor sem `key` por usuário, sinal não recriado no StrictMode, fallback aberto
antes da montagem — este sobreviveu à primeira rodada e ganhou teste próprio); W-02 11/11 detectadas
(sobreposição/subconjunto/superconjunto bastando, só-vazio, falha de leitura = pronto, só o primeiro
head, head fixo, sem recusa de diretório vazio, resultado ignorado, heads de lugar errado, vazamento de
revisões na resposta).

**Clerk (documentado, não validado):** ver "Bloqueios" em `SAAS_WEB_FOUNDATION.md` (claims `email` e
`email_verified` não são padrão; shortcodes documentados; limite de ~1,2 KB; alternativa pela Backend
API). A integração real com Clerk é tarefa separada.

## Pendências e riscos (não declarar como feito)

1. **Forma real do token do Clerk não validada**: a API exige `email` e `email_verified` (booleano)
   além de `azp`; o token padrão do Clerk não traz e-mail. Shortcodes confirmados só na documentação;
   falta confirmar numa instância real o tipo do valor e a semântica de `email_verified`.
2. Sem instância Clerk, PostgreSQL gerenciado, deploy, CSP, ESLint nem teste em navegador (ver
   "Bloqueios" no documento de arquitetura).
3. Um workspace por dono e papel único `owner` são decisões reversíveis de MVP.

## Próximos passos

Validar com um Clerk de desenvolvimento e registrar o formato real do token; Playwright contra
API+banco descartáveis; só depois homologação/deploy (com autorização). Cofre (S3) antes de
qualquer credencial real.
