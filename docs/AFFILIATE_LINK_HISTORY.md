# Histórico durável de links afiliados

Auditoria local, separada por banco e escopo (`runtime` ou `shadow`). Nesta entrega,
somente a geração AliExpress é integrada. Não há integração Mercado Livre, publicação,
backup automático, purge do histórico ou desbloqueio de tentativas incertas.

## Fatos e limites

- `affiliate_link_generations`: REQUESTED, PREPARED, CALL_STARTED, CONFIRMED,
  REJECTED, FAILED ou UNCERTAIN. REQUESTED não representa chamada TOP; REJECTED
  representa rejeição local, não necessariamente recusa da API.
- `affiliate_link_uses`: previews, saídas explícitas e reservas/tentativas de envio.
  Um uso pode ter vários links e uma geração pode ter vários usos.
- `affiliate_link_use_links`: referências RESTRICT às duas tabelas anteriores.
  Não existe cascade de evidence, proof ou preview para o histórico.
- `generated_at` é a aceitação original da resposta pelo bot. Cache hit referencia
  o UUID original e registra outro uso; não altera essa data nem representa nova TOP.
- Campos desconhecidos permanecem nulos com motivo. O histórico retém os fatos mínimos
  de contrato, tracking, correlação, origem disponível e validade operacional original.
- `tracking_confirmed` confirma a comparação exata do valor retornado pela API.
  `attribution_unverified=true` permanece obrigatório: geração, clique ou envio não
  comprovam comissão ou precedência sobre contexto herdado de shorts. A comprovação
  financeira exige evidência específica do Portals, fora desta entrega.
- Consultar um link expirado não autoriza sua reutilização nem dispensa contrato,
  correlação, tracking, TTL, gates, limites, locks ou deduplicação atuais.

## Armazenamento e recuperação

Geração real exige SQLite absoluto, local, em disco fixo, fora dos diretórios temporários
conhecidos, schema atualizado, FKs e integridade válidas, journal persistente e
`synchronous=FULL`. Não existe fallback para memória, URI de memória ou banco temporário.
A validação precede a construção dos transportes nos entrypoints e é repetida no cliente.
Chamadas programáticas LINK_GENERATE exigem contexto auditado e claim durável. O transporte
também valida o banco e a correspondência exata do formulário com esse contexto antes do POST.

`convert-preview` exige `--database` explícito; em `--scope runtime` deve corresponder
ao runtime configurado. `--scope shadow` seleciona explicitamente um banco shadow separado,
permitindo consumir uma solicitação canônica criada nesse mesmo banco. Nenhum fluxo busca
solicitações em outro banco. Comandos shadow de geração exigem `--shadow-database` absoluto.
Esses comandos não migram automaticamente o banco: atualize o schema separadamente antes
de uma execução futura autorizada. O teste live de geração exige
`ALIEXPRESS_LIVE_TEST_SHADOW_DATABASE` durável explícito; não foi executado nesta entrega.
As demonstrações usam exclusivamente adapters MockTransport e indicam
`durable_history_recorded=false`; não são gerações reais confirmadas.

`convert-preview` e `shadow-preview` retornam somente metadados por padrão. Para mostrar
o link e texto convertido no stdout explícito, use `--include-content` (também existente
em `coin-shadow-preview`). Somente essa opção registra um uso EXPLICIT_OUTPUT. Logs e
stderr não recebem esse conteúdo.

PREPARED e claim operacional são gravados na mesma transação. CALL_STARTED é confirmado
antes do POST (todos os resultados esperados de um lote na mesma transação), depois de
conferir posse, lease e orçamento. A resposta validada, histórico CONFIRMED e proof/evidence
são confirmados juntos. A tentativa não é rearmada por erro de commit ou reinício.
PREPARED vencida sem início vira FAILED; CALL_STARTED vencida vira UNCERTAIN. Essa
recuperação é uma transação separada antes do claim, para sobreviver a uma recusa posterior.
Canônicos ficam em revisão e shorts UNCERTAIN. Outra mensagem, TTL, troca de tracking
ou rotação do secret não desbloqueiam automaticamente uma tentativa desconhecida.
Uma geração CONFIRMED que expira normalmente pode gerar outra conforme os gates atuais.

Reserva, uso SEND_RESERVED e associações são atômicos. SEND_IN_FLIGHT é persistido antes
de sendMessage, que ocorre fora da transação. Reserva e resultado durável são finalizados
juntos: SEND_CONFIRMED, SEND_FAILED ou SEND_UNCERTAIN. Falha de persistência antes da rede
implica zero envio. Crash ou falha do commit final deixam uma reserva que bloqueia retry.
Na consulta read-only, SEND_IN_FLIGHT é apresentado também como resultado efetivo incerto,
sem modificar o banco nem afirmar que o envio falhou ou foi confirmado.

## Caches legados: transição de uma identidade

Não existe importação retroativa ou estado LEGACY_UNVERIFIED. Legado sem vínculo
CONFIRMED comprovado bloqueia cache, preview e envio, mesmo expirado, com
`AFFILIATE_HISTORY_GENERATION_LINK_MISSING` ou `AFFILIATE_HISTORY_GENERATION_LINK_INVALID`.
Migration, consulta e cache miss não apagam esse legado nem provocam TOP.

Quando uma evidence legada não possui contexto que permita comparar sua fingerprint após
rotação do secret, o bloqueio é conservador no banco shadow: GENERATING, UNCERTAIN ou
REVIEW_REQUIRED sem vínculo impedem novas gerações de shorts. READY sem vínculo também
não permite interpretar uma identidade desconhecida como cache miss. Isso pode bloquear
shorts não relacionados; não há desbloqueio automático nem reparo nesta entrega. READY
continua elegível somente para a transição explícita da identidade comprovável. Uma
fingerprint antiga que já não possa ser comparada ao input exige resolução futura separada.

Os comandos abaixo **não foram executados contra bancos do operador**. Escolha o mesmo
banco/escopo da identidade operacional que deseja consultar; obtenha os IDs dos metadados.

```powershell
cd C:\Users\felip\.codex\worktrees\durable-link-history\promo_bot
$historyDb = Join-Path $env:LOCALAPPDATA 'promo_bot\shadow\aliexpress-coin-listener-pilot.sqlite3'

uv run --offline promo-bot affiliate link-history legacy-blocks `
    --database $historyDb --scope shadow --platform aliexpress

uv run --offline promo-bot affiliate link-history request-legacy-generation `
    --database $historyDb --scope shadow --legacy-kind coin-evidence `
    --legacy-id $legacyEvidenceId --confirm-new-generation
```

A solicitação exige lock do banco e target elegível, cria REQUESTED e snapshot tipado,
imutável e rotulado LEGACY_NOT_REVALIDATED. Preserva URL antigo, identidade conhecida,
timestamps, contrato, fingerprints, indicadores e referências operacionais como declarações
do registro anterior, não validação nova. Não chama TOP/Telegram, não muda cache nem gates.
Repetir um target ainda pendente retorna a mesma solicitação.

Claims ativos, leases desconhecidas, CALL_STARTED, UNCERTAIN, coin GENERATING/REVIEW_REQUIRED
e outros bloqueios são recusados. A solicitação não serve para desbloqueá-los. Consumo
manual futuro usa `--generation-request UUID` somente em `convert-preview` ou
`coin-shadow-preview`, com gates novamente verificados, uma TOP máxima e zero retries.
Listeners/auto-delivery nunca consomem REQUESTED. Input deve corresponder à identidade;
o short literal vem da mensagem, nunca do URL afiliado arquivado. Mudança do target ou
de suas referências retorna AFFILIATE_HISTORY_LEGACY_TARGET_CHANGED sem TOP.

Upsert canônico preserva o snapshot anterior e vincula somente a nova geração ao proof.
Shorts substituem evidence/preview operacional atomicamente com o claim autorizado,
anulando o preview_id das reservas sem removê-las. IDs de preview não são reutilizados.
Previews antigos não são revalidados por igualdade de URL: um preview canônico cujo proof
mudou de geração permanece bloqueado, inclusive se o novo URL for idêntico. Use uma nova
mensagem autorizada para outro preview; isso não libera reservas anteriores nem reenvio.
Solicitação consumida não pode ser repetida. Falha/resultado incerto conserva o snapshot.

## Consulta read-only

Não carrega .env, cria transporte, migra, recupera leases ou executa purge. JSON ASCII seguro
para stdout. Logs/stderr nunca contêm URLs, tracking bruto, assinaturas, credenciais ou TOP
bruto. URL completo é sensível e aparece somente no stdout explícito com `--include-urls`.

```powershell
uv run --offline promo-bot affiliate link-history list `
    --database $historyDb --scope shadow --platform aliexpress `
    --generation-result CONFIRMED --limit 20

uv run --offline promo-bot affiliate link-history list `
    --database $historyDb --scope shadow --generation-result CONFIRMED `
    --send-result SEND_UNCERTAIN --used-after '2026-10-01T00:00:00-03:00'

uv run --offline promo-bot affiliate link-history show `
    --database $historyDb --scope shadow --generation-id $generationId

uv run --offline promo-bot affiliate link-history show `
    --database $historyDb --scope shadow --generation-id $generationId --include-urls
```

Período de geração: `--generated-after` (inclusive) / `--generated-before` (exclusivo).
Período de uso: `--used-after` / `--used-before`, independente do estado da geração.
Datas exigem fuso horário. `--send-result` é independente de `--generation-result`.
Listagens são limitadas a 1–200 itens. Para snapshot legado, somente
`show --include-legacy`; seu URL exige adicionalmente `--include-urls`.
Cada uso de envio conserva sua chave de destino (alias ou fingerprint, conforme o fluxo)
e o message ID devolvido em envio confirmado, mesmo após purge do preview operacional.

Exemplo sanitizado (UUID ilustrativo; não é evidência live):

```json
{"id":"<generation-uuid>","platform":"aliexpress","state":"CONFIRMED","tracking_confirmed":true,"attribution_unverified":true,"url_included":false,"history_authorizes_reuse":false}
```

Com exibição explícita, somente uma geração CONFIRMED ganha `generated_url` com seu URL
armazenado. Snapshot legado permanece LEGACY_NOT_REVALIDATED; não é geração CONFIRMED.

## Migration e retenção

Revision `9b3d5e7f1a20`, parent `7e2b9c4d5a10`, aditiva e sem backfill. Pointers UUID
operacionais são nullable, sem FK para tabelas históricas; todo dereference é validado.
FKs internas do histórico usam RESTRICT. Confirmações e snapshots são imutáveis.
Upgrade recusa órfãos existentes sem reparo automático. FKs de todos os handles SQLite
ficam habilitadas. Nenhuma expiração operacional apaga o histórico.

Retenção sem purge automático e sem backup automático nesta fase. Downgrade verifica
as três tabelas antes de DDL e recusa qualquer conteúdo (até REQUESTED/FAILED), com
AFFILIATE_HISTORY_DOWNGRADE_BLOCKED_NONEMPTY. Validações de migration usam somente
SQLite temporário: roundtrip vazio/check, upgrade representativo e recusa de órfão/
downgrade preenchido. A correção documental do piloto anterior continua separada.
