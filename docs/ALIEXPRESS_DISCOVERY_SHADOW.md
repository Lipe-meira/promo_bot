# Garimpo shadow do AliExpress

Este fluxo executa buscas manuais, privadas e limitadas pela operação oficial
`aliexpress.affiliate.product.query`. Ele não gera links afiliados, não usa Telegram, não cria
negócios e não publica promoções.

## Estado e limites do contrato

A chamada live isolada que antecedeu esta implementação confirmou acesso a `product.query` em
BR, BRL e PT, com `platform_product_type=ALL`. A resposta observada não deve ser generalizada:
campos de produto continuam opcionais e são normalizados de forma tolerante. `category.get`,
`hotproduct.query` e `product.smartmatch` estão fora deste MVP. A permissão Advanced API permanece
pendente e deve ser comprovada antes de habilitar hot products ou smart match.

O gate nasce fechado: `ALIEXPRESS_DISCOVERY_SHADOW_ENABLED=false`. Uma execução exige ainda o
provider AliExpress habilitado em `official_api`, `ALIEXPRESS_LIVE_API_ENABLED=true`, `DRY_RUN=true`
e `PUBLISH_REAL_DEALS=false`. O transporte usa `max_attempts=1`, sem retry durável, sem GET e sem
seguir redirects.

Cada perfil limita páginas, resultados e chamadas. O cache vale 60 minutos. Uma execução que
encontra um claim ativo termina imediatamente com `CONCURRENT_QUERY_IN_PROGRESS`: não espera, não
faz polling e não promete retomada. Uma execução manual posterior pode aproveitar o cache criado
pelo vencedor.

As identidades de query e tracking são fingerprints HMAC-SHA256 em domínios separados. Tracking,
segredo, payload assinado e resposta TOP bruta não são persistidos nem exibidos. A rotação do
tracking ou do secret invalida naturalmente o cache anterior.

## Normalização e histórico

`product_id` positivo é a única identidade obrigatória. O preço usado no ranking precisa vir de
`target_sale_price`, ser positivo e ter moeda BRL. URLs de produto, loja e promoção são ignoradas.
O cache preserva apenas campos normalizados e nunca cria um snapshot de preço.

A precedência dentro do run é `LIVE > CACHE`:

- cache seguido de live promove o resultado para live e cria no máximo um snapshot;
- live seguido de cache conserva o resultado live;
- entre duas observações live, a primeira válida vence;
- somente cache não cria snapshot.

O histórico usa snapshots live dos 30 dias anteriores e exige pelo menos dois runs distintos. A
primeira coleta é somente baseline. O preço original declarado pela loja não prova desconto real.
O score combina queda contra a mediana histórica, desconto declarado, volume, comissão e
completude, mas sempre mantém esses sinais separados. O resultado não comprova comissão e o fluxo
não publica nem entrega automaticamente.

## Comandos manuais futuros

O arquivo de perfis é separado da configuração principal para que hardware, periféricos e gamer
sejam apenas um conjunto inicial configurável, não categorias hardcoded.

```powershell
$env:ALIEXPRESS_DISCOVERY_SHADOW_ENABLED = "true"
$env:ALIEXPRESS_LIVE_API_ENABLED = "true"
$env:DRY_RUN = "true"
$env:PUBLISH_REAL_DEALS = "false"

uv run promo-bot aliexpress discovery-scan `
  --config .\config.yaml `
  --profiles .\.private\aliexpress-discovery-profiles.yaml `
  --profile hardware-gamer-br `
  --shadow-database "$env:LOCALAPPDATA\promo_bot\shadow\aliexpress-discovery.sqlite3"
```

A saída do scan contém somente metadados sanitizados. A inspeção explícita de produtos é separada:

```powershell
uv run promo-bot aliexpress discovery-results `
  --run-id 1 `
  --shadow-database "$env:LOCALAPPDATA\promo_bot\shadow\aliexpress-discovery.sqlite3" `
  --include-products
```

`--include-products` pode mostrar ID, título, preço, score e classificação no stdout solicitado. Os
logs e stderr não mostram palavras-chave, títulos, tracking, assinatura, credenciais, payloads,
URLs ou respostas brutas.

Preço, desconto e disponibilidade podem mudar a qualquer momento. A retenção e o ranking são
evidência observacional do próprio bot, não garantia comercial. Cupons, geração de links,
agendamento, entrega e publicação exigem fases e autorizações futuras separadas.
