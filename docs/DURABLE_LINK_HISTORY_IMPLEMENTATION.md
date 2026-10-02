# Entrega: histórico durável de links afiliados

Implementação local em `codex/durable-link-history`, numa única worktree:
`C:/Users/felip/.codex/worktrees/durable-link-history/promo_bot`.

Base local main/origin/main: `ac2b943516161e2935d8a3456bdd8cf2f1506efd`.
Sem push, merge, fetch nesta implementação, chamadas externas, geração real, Telegram
ou publicação. Nenhum banco do operador foi aberto ou migrado; .env real não foi carregado.

## Commits por ciclo

- `978acb18ed4f77d7e06ea168c852b10bd0ad03dc`: schema, armazenamento e migration.
- `e85141e37b7a579b0ffef964df6a76bef8d3dd74`: solicitação explícita e snapshot legado.
- `99c639201040c1ae858e65c9ba92bde6d6ed9533`: geração auditada, claims e recuperação.
- `f6e631a9b056afb5f49b7498fe315b5458641c04`: preview, cache e tentativa/resultado de envio.
- `9997f4313c4de6fb932ce2172ff27fbb26ba3794`: consulta read-only, CLI e documentação.
- `d4c9c38c0b3030a63f6836da8d630c4bde6d672c`: revisão da entrega anterior,
  `fix(affiliate): harden durable history boundaries`.
- `c9f6ca45f56a33325e52e0c58b8ce7b74dc27945`: correções dos dois desvios,
  `fix(affiliate): align legacy guards and conclusive rejections`.
- Commit documental posterior: `docs(affiliate): record durable history review corrections`;
  seu SHA é informado na entrega e em `git log`.

## RED → GREEN e verificações

Cada ciclo observou falha esperada antes da implementação mínima e testes focados GREEN:

| Etapa | Resultado focado |
|---|---|
| Schema/armazenamento | 18 testes |
| Transição legada | 14 testes |
| Geração/recuperação | 70 + 83 testes (grupos distintos, não totalizar com a suíte) |
| Usos/envios | 145 testes |
| Consulta/CLI/migration | 37 + 13 testes |
| Revisão final | 55 testes; grupo ampliado 28; consulta 5 |

Gates da entrega anterior (baseline, não validação das correções de 2026-10-02):

| Verificação | Resultado |
|---|---|
| `uv lock --check --offline` | passou; 41 packages |
| `uv run --offline ruff format --check .` | passou; 214 arquivos, incluindo documentação |
| `uv run --offline ruff check .` | passou |
| `uv run --offline mypy src` | passou; 98 arquivos source |
| `uv run --offline pytest -m "not live and not browser"` | **902 passaram, 5 excluídos, 5 warnings; 291,37 s** |
| `git diff --check` | passou |
| Migration focada | **6 passaram**, 1 warning; 8,87 s |

A suíte anterior inclui a adição da consulta de destino/envio após purge.
As correções posteriores são descritas abaixo e exigem nova suíte completa.

Baseline: 851 passaram, 5 externos excluídos, 4 warnings. O warning adicional é da
checagem da nova migration, sobre o ciclo preexistente candidates/proofs/deals.
Outros warnings são os adapters datetime preexistentes do SQLite/Python 3.12.
Os testes live de geração foram atualizados para exigir contexto e SQLite durável,
mas não foram executados.

Sockets externos bloqueados pelo pytest; settings sintéticas com .env desativado e
gates externos false; transportes falsos, inclusive simuladores assinados fisicamente
limitados a MockTransport. Demonstrações se declaram sintéticas e não registram
geração real durável.

## Migration

Revision `9b3d5e7f1a20`, parent `7e2b9c4d5a10`.

Validação em SQLite temporário externo: upgrade → downgrade → upgrade → check em
banco vazio; recusa de downgrade preenchido antes de DDL; upgrade representativo do
head anterior preservando dados/reservas/purge; recusa de órfão sem reparo; constraints,
imutabilidade e unicidade. Nenhuma migration foi aplicada aos bancos existentes.

## Revisão independente e correções

Uma única revisão fresca encontrou sete pontos importantes, todos com regressões offline:

1. Rotação do secret diante de evidence legada desconhecida: guardas conservadoras,
   sem autorizar chamada automática por fingerprint não comparável.
2. Transporte preparado: valida novamente armazenamento e formulário exato do
   contexto auditado antes de entrar na rede.
3. Segunda reconciliação canônica discordava do TTL e podia deixar PREPARED:
   removida; cache e claim já compartilham transação serializada.
4. Ponteiros operacionais: validação explícita de tipo de uso, escopo, origem,
   operação, ID e destino; uma reserva não pode atualizar o uso de outra.
5. Purge coin: valida geração/evidence integralmente; igualdade de URL não basta.
6. SEND coin preserva a indicação por link de reutilização de cache.
7. Saída canônica sensível exige --include-content e não gera EXPLICIT_OUTPUT
   apenas por produzir metadados.

A consulta também passou RED → GREEN para mostrar destino e ID do envio confirmado
depois do purge operacional. Transição coin preserva reservas com preview_id nulo,
não reutiliza o ID do preview antigo e não o confirma retroativamente, mesmo quando
o novo URL é igual ao antigo.

## Decisões, custos e limites

- Ponteiros UUID operacionais são nullable e sem FK para o histórico, evitando rebuild
  das tabelas cíclicas anteriores. Cada dereference relevante é validado; ponteiro
  inválido falha fechado. FKs internas do histórico são RESTRICT.
- Todos os handles SQLite passam a habilitar FKs e synchronous=FULL. Dados anteriores
  com órfãos exigem resolução explícita externa; nenhum reparo automático.
- Recuperação usa uma transação confirmada antes do claim, para que uma recusa não
  reverta UNCERTAIN. Consumo REQUESTED/claim/PREPARED continua atômico.
- Preview canônico cujo proof foi atualizado para outra geração fica bloqueado;
  precisa de nova mensagem autorizada, sem remover reservas ou liberar reenvio.
  IDs coin usam high watermark durável após substituição explícita.
- --scope shadow em convert-preview seleciona apenas o banco shadow indicado.
  Os comandos de geração não migram bancos automaticamente; o operador precisa
  aprovar separadamente a atualização do schema antes de uma operação real.
- Legado coin não comparável mantém bloqueio conservador, mas input coincidente ou
  tracking HMAC contextualizado coincidente permitem distinguir identidades com os
  dados existentes. Um legado comprovadamente distinto não bloqueia outro short.
  READY/REVIEW_REQUIRED não comparáveis retornam LEGACY_KEY_CONTEXT_UNPROVEN; tentativas
  desconhecidas permanecem bloqueadas. Não há recuperação da correspondência perdida
  nem desbloqueio de UNCERTAIN nesta fase. A CLI sem chave/input informa elegibilidade
  do registro separada da correspondência UNPROVEN, sem garantir execução.
- Uma segunda observação concorrente de falha pode receber diagnóstico de histórico
  bloqueado em vez do erro inicial do parser; não há chamada extra e a rejeição fica registrada.
- Limitação menor mantida: abertura programática de banco inexistente pode deixar
  arquivo vazio antes da recusa de schema, mas nunca chama TOP.
- JSON integralmente decodificado não-objeto agora é REJECTED, não UNCERTAIN.
  Falha de commit da rejeição continua sujeita a UNCERTAIN. Nenhuma tentativa anterior
  é reclassificada nem solicitação consumida rearmada.
- Dados históricos não autorizam reuse fora do TTL nem substituem tracking/contrato,
  correlação, destino, gates, lock, budgets ou deduplicação.
- Nenhum backfill, purge do histórico, backup automático, Mercado Livre, publicação,
  evidência de comissão ou desbloqueio incerto foi implementado. Tracking confirmado
  não comprova comissão nem precedência sobre contexto herdado.
- A correção documental anterior do piloto permanece pendente e separada.
- Nenhuma obrigação de teste live/Telegram/Portals/migration dos bancos do operador
  foi executada: estão fora da autorização. Não houve segunda rodada de sub-review.

## Preservação

F:/projetos/promo_bot continua no checkout anterior e com .env.example e
config.example.yaml modificados pelo operador. Seus hashes, e o de .gitignore, foram
comparados ao inventário inicial e permanecem iguais. Nenhum desses arquivos aparece
nos commits desta feature. .env/config.yaml não foram copiados para a worktree.

As demais worktrees e bancos foram preservados. A branch main não foi modificada.
A limpeza final remove apenas o scratch de validação criado para esta tarefa,
não worktrees, .venv ou arquivos do operador.

## Correções da revisão — 2026-10-02

Mesma branch/worktree, sem nova migration, push, merge, .env real, bancos do operador
ou transportes externos. RED observado para bloqueio de identidade distinta, diagnóstico
de chave ausente, acordo dos comandos, contexto durável nulo e JSON não-objeto nos dois
caminhos. GREEN focado confirma transição com snapshot, disputa de solicitação, reinício,
nenhuma reclassificação de UNCERTAIN e falha real de escrita simulada por trigger SQLite.
Uma rotação de chave também não transforma REJECTED em nova autorização de chamada.

O scanner, publicação, Mercado Livre e a pendência documental do piloto não mudaram.
Os cinco warnings preexistentes não são corrigidos aqui: três SAWarning do ciclo
candidates/proofs/deals na comparação Alembic; dois DeprecationWarning do adapter datetime
em SQL direto do teste SKU. FKs permanecem habilitadas; as validações temporárias continuam
necessárias. A abertura programática de arquivo inexistente pode deixar arquivo vazio
antes da recusa de schema, sem TOP, como já documentado.

Gates frescos desta correção, sobre o código de `c9f6ca45` (não os 902 testes anteriores):

| Verificação | Resultado |
|---|---|
| `uv lock --check --offline` | passou; 41 packages |
| `uv run --offline ruff format --check .` | passou; 215 arquivos |
| `uv run --offline ruff check .` | passou |
| `uv run --offline mypy src` | passou; 98 arquivos source |
| Testes focados de histórico/transição/geração/usos/CLI | **56 passaram**; 40,50 s |
| `uv run --offline pytest -m "not live and not browser"` | **929 passaram, 5 excluídos, 5 warnings**; 262,67 s |
| Migration temporária focada | **6 passaram**, 1 warning; 9,11 s |
| `git diff --check` | passou |

Os 27 testes novos abrangem os cenários reproduzidos: outro short com legado READY
expirado; estados legados comprovadamente distintos; bloqueio do próprio alvo;
READY com outro UNCERTAIN separável ou não; contexto perdido; dois consumidores e
reinício da solicitação; contexto durável ausente; JSON completo não-objeto nos dois
caminhos; timeout/JSON possivelmente truncado; falha de gravação e saída sanitizada.
Nenhuma edição funcional ocorreu após essa suíte completa. O commit posterior altera
somente a documentação. Sockets externos bloqueados, .env desabilitado, settings
sintéticas e gates externos false; transportes falsos. Migration somente em SQLite
temporário: upgrade/downgrade/upgrade/check, recusa não vazia antes de DDL, upgrade
representativo anterior com purge/FKs e recusa de órfãos sem reparo.

Limitação restante explícita: os dados antigos não permitem sempre comprovar contexto
comparável. Nesses casos a execução permanece bloqueada e não há saída operacional
nesta entrega; os comandos sem chave/input não garantem correspondência. Isso não
é desbloqueio de UNCERTAIN nem confirmação retroativa. Não foi identificado bloqueador
remanescente de aderência nos dois desvios corrigidos; as demais limitações já descritas
e a pendência documental separada do piloto permanecem.

## Arquivos alterados (52)

Caminhos relativos à worktree isolada indicada acima:

```text
docs/AFFILIATE_LINK_HISTORY.md
docs/DURABLE_LINK_HISTORY_IMPLEMENTATION.md
migrations/versions/9b3d5e7f1a20_durable_affiliate_history.py
src/promo_bot/affiliate/aliexpress_conversion.py
src/promo_bot/affiliate/aliexpress_demo.py
src/promo_bot/affiliate/coin_shadow_generation.py
src/promo_bot/affiliate/coin_shadow_preview.py
src/promo_bot/affiliate/history_cli.py
src/promo_bot/affiliate/history_context.py
src/promo_bot/affiliate/history_query.py
src/promo_bot/affiliate/shadow_delivery.py
src/promo_bot/cli.py
src/promo_bot/database/coin_shadow_repository.py
src/promo_bot/database/history_models.py
src/promo_bot/database/history_repository.py
src/promo_bot/database/history_storage.py
src/promo_bot/database/models.py
src/promo_bot/database/repositories.py
src/promo_bot/database/session.py
src/promo_bot/database/shadow_delivery_repository.py
src/promo_bot/providers/aliexpress/client.py
src/promo_bot/providers/aliexpress/transport.py
tests/__init__.py
tests/conftest.py
tests/fixtures/aliexpress_redirect_cli_harness.py
tests/fixtures/aliexpress_terminal_cli_harness.py
tests/live/test_aliexpress_live.py
tests/offline_aliexpress.py
tests/offline_history.py
tests/offline_history_facts.py
tests/offline_shadow_runtime.py
tests/unit/__init__.py
tests/unit/test_affiliate_history_cli.py
tests/unit/test_affiliate_history_corrections.py
tests/unit/test_affiliate_history_generation.py
tests/unit/test_affiliate_history_legacy.py
tests/unit/test_affiliate_history_review.py
tests/unit/test_affiliate_history_schema.py
tests/unit/test_affiliate_history_storage.py
tests/unit/test_affiliate_history_uses.py
tests/unit/test_affiliate_shadow_previews.py
tests/unit/test_aliexpress_auto_delivery.py
tests/unit/test_aliexpress_contracts.py
tests/unit/test_aliexpress_conversion.py
tests/unit/test_aliexpress_shadow_listener.py
tests/unit/test_aliexpress_telegram_shadow.py
tests/unit/test_cli.py
tests/unit/test_coin_listener_pilot.py
tests/unit/test_coin_shadow_evidence.py
tests/unit/test_shadow_delivery.py
tests/unit/test_shadow_delivery_cli.py
tests/unit/test_shadow_delivery_migration.py
```

## Consulta futura (não executada contra bancos reais)

Escolha explicitamente o banco do mesmo fluxo; banco antigo sem schema histórico
falha com diagnóstico sanitizado, sem migration automática ou leitura de .env.
Os comandos de consulta são read-only e não executam TOP/Telegram/recuperação/purge.

```powershell
cd C:\Users\felip\.codex\worktrees\durable-link-history\promo_bot

$historyDb = Join-Path $env:LOCALAPPDATA 'promo_bot\shadow\aliexpress-coin-listener-pilot.sqlite3'

uv run --offline promo-bot affiliate link-history list `
    --database $historyDb --scope shadow --platform aliexpress `
    --generation-result CONFIRMED --limit 20

uv run --offline promo-bot affiliate link-history list `
    --database $historyDb --scope shadow `
    --generation-result CONFIRMED --send-result SEND_UNCERTAIN

uv run --offline promo-bot affiliate link-history show `
    --database $historyDb --scope shadow --generation-id $generationId

uv run --offline promo-bot affiliate link-history show `
    --database $historyDb --scope shadow --generation-id $generationId --include-urls

uv run --offline promo-bot affiliate link-history legacy-blocks `
    --database $historyDb --scope shadow --platform aliexpress
```

O UUID deve vir da listagem, não ser presumido. Consulta de snapshot legado exige
show --include-legacy; seu URL exige também --include-urls. Solicitação de nova geração
é ação de escrita separada, descrita em AFFILIATE_LINK_HISTORY.md; não equivale a
autorizar rede ou envio.
