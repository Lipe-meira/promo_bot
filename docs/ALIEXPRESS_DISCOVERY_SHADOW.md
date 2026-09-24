# Garimpo shadow do AliExpress

Este fluxo executa buscas manuais, privadas e limitadas pela operação oficial
`aliexpress.affiliate.product.query`. Opcionalmente, um run isolado pode usar
`aliexpress.affiliate.hotproduct.query`. Ele não gera links afiliados, não usa Telegram, não cria
negócios e não publica promoções.

## Estado e limites do contrato

A chamada live isolada que antecedeu esta implementação confirmou acesso a `product.query` em
BR, BRL e PT, com `platform_product_type=ALL`. A resposta observada não deve ser generalizada:
campos de produto continuam opcionais e são normalizados de forma tolerante. Um spike isolado de
`hotproduct.query` retornou cinco itens com `target_sale_price` positivo em BRL; isso não comprova
consistência futura nem equivalência de preços com `product.query`. `category.get` e
`product.smartmatch`: cada um permanece fora do escopo. A Advanced API está **Active**, mas isso não abre
automaticamente o caminho hot: ele tem gate próprio.

O gate nasce fechado: `ALIEXPRESS_DISCOVERY_SHADOW_ENABLED=false`. Uma execução exige ainda o
provider AliExpress habilitado em `official_api`, `ALIEXPRESS_LIVE_API_ENABLED=true`, `DRY_RUN=true`
e `PUBLISH_REAL_DEALS=false`. O transporte usa `max_attempts=1`, sem retry durável, sem GET e sem
seguir redirects.

`discovery-scan` usa `--source product-query` por padrão. Para um único run hot, é necessário
também `ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_ENABLED=true`. A seleção de fonte é exclusiva por
run; não há mescla automática. O perfil hot deve ter `page_size≤5`, `max_pages=1` por palavra-chave,
`max_api_calls≤2` no total e `max_results≤10`. Perfis acima desses tetos falham antes do
transporte; o gate sozinho não inicia chamadas. Nenhum campo `fields` é enviado.

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

O histórico usa snapshots live dos 30 dias anteriores **da mesma operação** e exige pelo menos dois
runs distintos. Um produto visto nas duas operações mantém duas observações com origem, instante
e preço próprios; cache e claim também são separados pela operação na fingerprint. O histórico SKU
continua separado por `(product_id, sku_id)` e nunca entra no baseline product-level. A primeira
coleta é somente baseline. O preço original declarado pela loja não prova desconto real. O score
combina queda contra a mediana histórica, desconto declarado, volume, comissão e completude, mas
mantém esses sinais separados. `PROVIDER_DISCOUNT_ONLY` não é queda histórica comprovada. Preço de
ambas as operações é product-level, não preço de SKU. O resultado não comprova comissão e o fluxo
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

Para uma busca hot manual, use o mesmo comando com `--source hotproduct` e habilite
`ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_ENABLED=true` somente para a execução pretendida,
mantendo o perfil dentro dos tetos acima. Cada run chama apenas a operação selecionada, com uma
tentativa por página e sem fallback para a outra fonte.

A saída do scan contém somente metadados sanitizados. A inspeção explícita de produtos é separada:

```powershell
uv run promo-bot aliexpress discovery-results `
  --run-id 1 `
  --shadow-database "$env:LOCALAPPDATA\promo_bot\shadow\aliexpress-discovery.sqlite3" `
  --include-products
```

`discovery-results` mostra `source_operation` nos metadados; `--include-products` acrescenta a
operação e `origin=LIVE|CACHE` a cada produto, além de ID, título, preço, score e classificação
no stdout solicitado. Os
logs e stderr não mostram palavras-chave, títulos, tracking, assinatura, credenciais, payloads,
URLs ou respostas brutas.

O campo `canonical_product_url` aparece somente com `--include-products` e é derivado localmente do
`product_id` ASCII positivo no formato exato
`https://pt.aliexpress.com/item/<product_id>.html`. Ele não é persistido, aberto ou enriquecido com
query string, fragmento, tracking ou qualquer URL devolvida pela API.

Preço, desconto e disponibilidade podem mudar a qualquer momento. A retenção e o ranking são
evidência observacional do próprio bot, não garantia comercial. Cupons, geração de links,
agendamento, entrega e publicação exigem fases e autorizações futuras separadas.

## Refinamento manual por SKU

O refinamento SKU é um segundo fluxo shadow, acionado manualmente depois de um
`discovery-scan` concluído. Ele usa somente
`aliexpress.affiliate.product.sku.detail.get`; não altera o scanner product-level e não usa
`productdetail.get` como substituto para propriedades de variação. A permissão
SKU Dimension API continua **Pending** até confirmação explícita. Os gates nascem fechados:

```text
ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED=false
ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED=false
```

Uma execução exige também todos os gates do discovery. Cada produto sem cache consome no máximo
uma chamada TOP, com uma tentativa e sem retry, GET, redirect, HTML ou browser. O cache SKU vale
15 minutos. Um claim concorrente encerra imediatamente com
`CONCURRENT_SKU_QUERY_IN_PROGRESS`; não existe polling nem retomada automática.

O preço de `product.query` (assim como de `hotproduct.query`) continua product-level e é
apresentado conceitualmente como
`PRODUCT_MINIMUM_UNVERIFIED_BY_SKU`. Ele nunca entra no baseline por SKU. O refinamento compara
somente propriedades estruturadas do mesmo SKU, aplicando `strip()` e `casefold()`; não converte
unidades e não usa o título do produto. Todos os requisitos configurados devem ser satisfeitos pelo
mesmo SKU.

- um candidato válido e único pode ficar `MATCHED`;
- nenhum candidato fica `NO_MATCH`;
- dois ou mais candidatos ficam `AMBIGUOUS`, sem seleção automática;
- propriedades ou preços malformados ficam `REVIEW_REQUIRED`;
- exatamente 20 resultados ficam `REVIEW_REQUIRED` com `POSSIBLE_SKU_TRUNCATION`, pois a
  unicidade pode não estar comprovada.

Somente um `MATCHED` obtido live, com `sale_price_with_tax` positivo em BRL, cria snapshot. Cache
não cria uma nova observação. O histórico usa apenas o mesmo `(product_id, sku_id)`, dois runs
anteriores distintos e a janela de 30 dias. A classificação
`SKU_HISTORY_BACKED_PRICE_DROP` exige a queda mínima do perfil; caso contrário, um match válido
fica `SKU_BASELINE_ONLY`. O score product-level serve apenas para ordenar a shortlist e como
desempate contextual, nunca como preço do SKU.

O contrato documentado não comprova estoque. O refinamento também não comprova comissão, não
gera link, não envia ao Telegram e não publica.

### Comandos futuros, após confirmação da permissão

```powershell
$env:ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED = "true"
$env:ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED = "true"

uv run promo-bot aliexpress discovery-sku-refine `
  --config .\config.yaml `
  --profiles .\.private\aliexpress-discovery-profiles.yaml `
  --profile hardware-gamer-br `
  --run-id 1 `
  --shadow-database "$env:LOCALAPPDATA\promo_bot\shadow\aliexpress-discovery.sqlite3"

uv run promo-bot aliexpress discovery-sku-results `
  --run-id 1 `
  --shadow-database "$env:LOCALAPPDATA\promo_bot\shadow\aliexpress-discovery.sqlite3" `
  --include-skus
```

Sem `--include-skus`, a inspeção retorna somente contadores sanitizados. A flag pode mostrar IDs,
atributos, preço, origem, estado e classificação apenas no stdout solicitado; logs e stderr não
mostram esses dados.

Antes de habilitar o fluxo, ainda é necessário um experimento live separado e autorizado, com
uma única chamada, para confirmar o envelope real, os nomes e valores estruturados das dimensões,
a presença de `sale_price_with_tax` em BRL e a ausência de truncamento. A configuração de uma
variação real deve usar somente grafias observadas nesse experimento.
