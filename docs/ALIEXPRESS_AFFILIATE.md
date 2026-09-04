# AliExpress Affiliate — contrato offline e gate operacional

## Estado deste marco

Este marco implementa somente o que pode ser comprovado por documentação oficial, pelo pacote
sanitizado coletado no AliExpress Open Platform e por uma consulta live isolada posteriormente
autorizada. Não houve uso do API Testing Tool, scraping, navegador, publicação no Telegram ou
processamento de pedidos.

O provider permanece desabilitado em `config.example.yaml`. O cliente TOP real existe somente atrás
do gate explícito `ALIEXPRESS_LIVE_API_ENABLED`, desabilitado por padrão, e não está conectado ao
pipeline de enriquecimento ou publicação. `UnavailableAliExpressAffiliateClient` e
`ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE` continuam preservados; um stub indisponível não
simula sucesso.

## Operações documentadas

Os payloads e parsers offline cobrem:

- `aliexpress.affiliate.productdetail.get`;
- `aliexpress.affiliate.product.query` — apenas contrato; busca permanece desativada;
- `aliexpress.affiliate.link.generate`;
- `aliexpress.affiliate.product.sku.detail.get`;
- `aliexpress.affiliate.product.shipping.get`;
- `aliexpress.affiliate.promotion.info.get`.

São aceitos os envelopes Streamlined e Non-Refinement observados no material oficial. Valores
monetários usam `Decimal`, identificadores são preservados como strings e campos não garantidos são
opcionais. Os JSONs versionados em `tests/fixtures/aliexpress` são sintéticos e sanitizados; PDFs,
capturas, dados pessoais, chaves e respostas live não fazem parte do Git.

## Invariantes de segurança e negócio

- A identidade canônica é o `product_id` numérico; uma seleção de SKU confirmada acrescenta
  `?sku_id=<id>` e participa da deduplicação como `variation_key`.
- Um preço de SKU não é anunciado como preço geral. Sem SKU selecionado, uma faixa documentada é
  exibida como faixa; não é convertida em preço exato.
- Preço não positivo, moeda diferente de BRL, SKU não confirmado ou produto indisponível impedem
  `READY`.
- Frete só aceita preço e taxa provenientes de uma operação oficial de produto/SKU.
- `promotion.info.get` permanece indisponível para destino BR, pois BR não consta na lista anexada.
  Promoção, campanha e cupom não são tratados como equivalentes.
- Um `promotion_link` encontrado em respostas de produto não é prova afiliada. A prova persistida
  deve vir exatamente de `link.generate`, corresponder ao `source_value` solicitado e usar o host
  documentado `s.click.aliexpress.com`.
- `deal.affiliate_link` é o mesmo link armazenado na prova; o enriquecimento não cria entrega na
  outbox. Com `PUBLISH_REAL_DEALS=false`, nada é enviado ao chat operacional.
- Logs e comandos mostram apenas presença booleana de credenciais, nunca valores.

## Persistência e retomada

Os candidatos AliExpress reutilizam `affiliate_candidates`, incluindo claim atômico, lease,
tentativas, backoff, recuperação e deduplicação. A migration cria apenas
`aliexpress_product_snapshots`, necessária para preservar evidência específica de produto, SKU,
faixa de preço, comissão, frete e operação-fonte. Produtos, negócios, histórico de preço, prova
afiliada e outbox continuam nas estruturas compartilhadas.

Uma transição final perdida ou uma lease expirada gera erro e reverte produto, snapshot, prova e
negócio na mesma transação.

## CLI offline

```powershell
uv run promo-bot aliexpress status
uv run promo-bot aliexpress preview --url "https://pt.aliexpress.com/item/1005000000000001.html"
```

`status` não faz rede e informa apenas os gates e booleanos de configuração. `preview` somente
canonicaliza o URL; não cria prova, `READY`, outbox ou entrega.

## Evidência do SDK Java oficial

A análise offline usou o source JAR Maven `com.global.iop:iop-api-sdk:1.3.5-ae`, SHA-256
`6B79FA214FD326215FB91268F1EE904D2CE037C40377911AF74726392EC1A1A8`. Foram inspecionados
`TopExecutor`, `BaseExecutor`, `RequestContext`, `IopUtils`, `IopHashMap`, `WebUtils` e as seis
classes `AliexpressAffiliate*Request` correspondentes às operações autorizadas. O JAR não foi
executado nem incluído no repositório.

### Endpoint TOP comprovado por evidência combinada

A página oficial [Call API with official SDK](https://openservice.aliexpress.com/doc/doc.htm#/?docId=1371),
mostrada com atualização de 2024-03-29, declara `https://api-sg.aliexpress.com` como service
endpoint. O exemplo visível instancia `TopClientImpl` com esse `serverUrl` e executa uma operação de
nome pontuado por `execute(..., Protocol.TOP)`.

Os dois prints recebidos foram conferidos offline e não serão versionados:

- `codex-clipboard-f6012785-1e1a-4c9a-8502-e518dea48fb4.png`, SHA-256
  `A9F4583985154440A81D7118EC416817C69C9DD6E93F2345CB64C9BD96D27D0A`;
- `codex-clipboard-9f1c3ce9-3e4b-4eed-9d6e-17562d7eee80.png`, SHA-256
  `9B6805B90B59D48F66E9070D9AC2E84B3E2F2D76EABA50245BEDF7C9D9D25E08`.

A página não escreve a URL completa `/sync` no trecho capturado. Essa parte vem do source JAR:
`TopExecutor` constrói `serverUrl + "/sync?method=" + apiName`. A composição das duas fontes
oficiais comprova o endpoint TOP `https://api-sg.aliexpress.com/sync` para as operações pontuadas,
inclusive as seis classes Affiliate inspecionadas. O host vem da documentação; o path e a query de
roteamento vêm do SDK.

### Comportamento comprovado do signer

Para TOP, o SDK monta um único mapa canônico com os parâmetros comuns e de negócio. Chaves ou
valores nulos, vazios ou compostos apenas por espaços não participam. As chaves restantes são
ordenadas lexicograficamente e o texto assinado concatena cada chave imediatamente ao seu valor,
sem separadores, prefixo de nome de API ou sufixo de corpo. `method` aparece uma vez nesse mapa e
`sign` só é acrescentado depois do cálculo.

O segredo e o texto canônico usam UTF-8. O digest é HMAC-SHA256 e o resultado é hexadecimal em
maiúsculas. O timestamp vem de `System.currentTimeMillis()` e é serializado como milissegundos Unix.
Os parâmetros comuns observados são:

- `app_key`;
- `v=2.0`;
- `timestamp`;
- `method`;
- `format=json`;
- `session`, quando presente;
- `partner_id=iop-sdk-java-20181207`;
- `sign_method=sha256`;
- `simplify=true` por padrão;
- `debug=true`, quando habilitado;
- `sign`, somente após o cálculo.

### Peculiaridade de compatibilidade do SDK

`TopExecutor` inicia a URL relativa como `/sync?method=<operação>`. Depois, `BaseExecutor` anexa os
parâmetros comuns, que contêm outro par `method` com o mesmo valor. Assim, o wire produzido pelo SDK
Java contém duas ocorrências idênticas de `method`: a primeira de roteamento e a segunda dos
parâmetros comuns. Isso é uma peculiaridade de compatibilidade observada, não um contrato normativo
confirmado da API. O conteúdo canônico assinado continua contendo `method` apenas uma vez.

O layout observado usa `POST`, parâmetros comuns na query e parâmetros de negócio em um formulário
`application/x-www-form-urlencoded;charset=UTF-8`. A representação Python preserva a query como
pares ordenados imutáveis, portanto não perde a duplicação como ocorreria com um `dict` simples e
não cria uma terceira ocorrência ao compor a URL relativa.

### Decisão determinística da implementação Python

O SDK Java usa `HashMap`; portanto, ele não demonstra que a ordem física dos pares na query ou no
formulário faça parte do contrato. Para tornar os testes offline reproduzíveis, a implementação
Python mantém o primeiro `method` de roteamento e ordena os demais pares da query e os pares do
formulário. Essa ordenação física é uma decisão local. Somente a ordenação lexicográfica usada para
compor a assinatura está comprovada pelo signer do SDK.

O módulo `promo_bot.providers.aliexpress.top` prepara apenas método, path, query e formulário
relativos. Ele aceita exclusivamente as seis operações Affiliate documentadas, rejeita colisões
com nomes TOP reservados e sanitiza suas representações. Ele não implementa I/O nem seleciona host.
`AliExpressHttpTransport` consome a requisição preparada, e `AliExpressAffiliateApiClient` conecta
os dois somente atrás do gate live explícito.

### Validação live sanitizada

Em 2026-09-04, o operador executou localmente e com autorização explícita somente o teste
`tests/live/test_aliexpress_live.py::test_one_known_product_detail_without_publication_or_database`.
O resultado informado foi `1 passed in 2.52s`. O teste fez uma única consulta de leitura para
`aliexpress.affiliate.productdetail.get`, encontrou o produto conhecido na resposta parseada e não
inicializou banco, pipeline, scheduler ou Telegram.

Essa execução confirma, para o ambiente real usado pelo operador, o gateway TOP, a assinatura, as
credenciais, a permissão da aplicação e a operação `aliexpress.affiliate.productdetail.get`. O
registro não inclui status HTTP numérico, `request_id`, credenciais, assinatura, `tracking_id`, URL
assinada, formulário, headers ou corpo bruto da resposta.

Na mesma data, o operador executou localmente e com autorização separada somente o teste
`tests/live/test_aliexpress_live.py::test_one_known_link_generate_without_publication_or_database`.
O resultado informado foi `1 passed in 1.73s`. A execução fez uma única chamada para
`aliexpress.affiliate.link.generate` e confirmou a correlação do `source_value` pelo mesmo
`product_id`, permitindo normalização da URL pela API, além do retorno de exatamente um link HTTPS
no host `s.click.aliexpress.com`. O link não foi aberto nem seguido, e banco, pipeline, scheduler e
Telegram não foram inicializados.

O registro desse segundo teste também não inclui o link completo, credenciais, assinatura,
`tracking_id`, URL assinada, query, formulário, headers, `request_id` ou corpo bruto da resposta.
As outras quatro operações Affiliate permanecem sem validação live. Rate limits e respostas de
erro reais também permanecem desconhecidos. O provider continua desabilitado, todos os gates de
publicação permanecem ativos e o cliente TOP não está conectado ao pipeline. O bypass de
certificado e hostname presente no SDK Java não foi reproduzido. Qualquer nova operação live exige
autorização separada.
