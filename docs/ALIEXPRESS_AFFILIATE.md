# AliExpress Affiliate — contrato offline e gate operacional

## Estado deste marco

Este marco implementa somente o que pode ser comprovado por documentação oficial, pelo pacote
sanitizado coletado no AliExpress Open Platform e por uma consulta live isolada posteriormente
autorizada. Não houve uso do API Testing Tool, scraping, navegador, publicação no Telegram ou
processamento de pedidos.

O provider permanece desabilitado em `config.example.yaml`. O cliente TOP real existe somente atrás
do gate explícito `ALIEXPRESS_LIVE_API_ENABLED`, desabilitado por padrão. A conversão de mensagens
persistidas está disponível por comando local de DRY_RUN; o cliente continua sem conexão automática
ao listener, ao pipeline de enriquecimento ou à publicação. `UnavailableAliExpressAffiliateClient` e
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
  A única saída com texto convertido e link afiliado é o preview local explicitamente solicitado;
  seu conteúdo não é enviado aos logs gerais.

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

## Conversão de mensagens recebidas — primeira fase DRY_RUN

O comando `aliexpress convert-preview` conecta uma mensagem já persistida pelo relay ao cliente
TOP. A execução é explícita, por ID interno de `source_messages`; não é um worker automático do
listener. O original permanece intacto e o resultado convertido existe somente no preview.
Esta fase gera apenas `aliexpress.affiliate.link.generate`, com uma URL, `promotion_link_type=0`
e `ship_to_country=BR`. Não consulta produto, preço, estoque ou cupom, não cria `Deal`, outbox ou
entrega e não inicializa Telegram ou scheduler.

### URLs e preservação do texto

- Entrada HTTPS em `aliexpress.com`, `www.aliexpress.com`, `pt.aliexpress.com` ou
  `de.aliexpress.com`, com path exato `/item/<product_id numérico>.html`, sem porta explícita,
  credenciais na autoridade ou fragmento.
- Parâmetros de tracking reconhecidos pelo canonicalizador são removidos somente da URL enviada
  à API. Uma seleção numérica inequívoca `sku_id`/`skuId` é preservada na identidade como
  `variation_key`; parâmetros desconhecidos ou seleções conflitantes são rejeitados.
- `a.aliexpress.com` e `s.click.aliexpress.com` são recusados com
  `ALIEXPRESS_SHORT_URL_UNSUPPORTED`, antes do expansor. Outros hosts ou caminhos AliExpress não
  canônicos recebem `ALIEXPRESS_CANONICAL_URL_REQUIRED`. Suporte seguro a links curtos será uma
  etapa posterior; nenhuma resolução remota ou redirecionamento é feito nesta conversão.
- São aceitos links explícitos no texto (`TEXT`/`ENTITY_URL`). Links ocultos e botões AliExpress
  recebem `ALIEXPRESS_TEXT_LINK_REQUIRED`; adaptar entidades e botões fica para etapa posterior.
- Mais de uma URL AliExpress distinta na mensagem é rejeitada atomicamente como
  `ALIEXPRESS_MULTIPLE_LINKS_AMBIGUOUS`, inclusive quando corresponde ao mesmo produto. Repetições
  da mesma URL explícita têm correlação inequívoca e podem ser substituídas juntas.
- Somente ocorrências completas da URL são substituídas. Pontuação, quebras de linha, texto e
  links de outras lojas permanecem intactos, inclusive quando contêm a URL AliExpress na query.
- A resposta deve conter exatamente um resultado correlacionado pelo `product_id`, permitindo
  normalização do `source_value`, e link HTTPS no host `s.click.aliexpress.com`. Falha impede o
  preview e a criação de prova; não há fallback para link não afiliado. O retorno não é aberto.
  A correlação pelo produto não comprova a seleção final de SKU no destino do redirecionamento.

### Cache, expiração e trabalho durável

São reutilizadas `source_messages`, `source_message_links`, `affiliate_candidates` e
`affiliate_link_proofs`. O candidato identifica `(store, product_id, variation_key)`; o cache
exige também `promotion_link_type`, fingerprint da configuração e prova validada não expirada.
A fingerprint é HMAC-SHA256, com `app_secret` como chave e versão de contexto, `app_key` e
`tracking_id` como entrada. Apenas a impressão de 64 caracteres é persistida, nunca o tracking
configurado em texto aberto. Rotação de qualquer credencial/tracking causa cache miss.

O TTL Python é explicitamente **24 horas a partir de `responded_at`**, não uma garantia de validade
fornecida pela API. `requested_at`, `responded_at`, `created_at` e `expires_at` registram a emissão
atual; ao renovar a prova, esses horários são atualizados. Expiração, metadados legados nulos,
mudança de tipo ou fingerprint exigem nova geração. Horário de resposta no futuro não é reutilizado.
O banco continua contendo o link afiliado da prova e o texto original, portanto permanece dado
local privado, fora do Git e dos logs.

A migration aditiva `d8a31f67c2b4` acrescenta `promotion_link_type`, `tracking_fingerprint` e
`expires_at`, todos anuláveis, na tabela existente. O schema anterior não representava essas
dimensões sem sobrecarregar campos de outro significado. Provas antigas não ganham validade
retroativa e não são reutilizadas. A migration está em commit separado e não é aplicada
automaticamente pelo preview. Nesta implementação ela foi executada apenas nos bancos de teste.

O processamento reutiliza claim/lease do candidato, com lease de 5 minutos, até 3 tentativas e
`BackoffPolicy` existente (60 segundos iniciais, exponencial, jitter de até 20%, teto de 300
segundos). Cada tentativa faz uma chamada HTTP; novas tentativas são comandadas explicitamente,
respeitando `next_attempt_at`. O estado final é `AFFILIATE_GENERATED`, sem transformar o candidato
em negócio publicável. Prova e transição são gravadas na mesma transação. A conclusão e a falha
verificam início e número da tentativa: um worker antigo não pode finalizar uma lease mais recente.

### Gates e preview local

Para converter uma mensagem persistida, o comando exige provider `aliexpress.enabled=true`,
`affiliate_mode=official_api`, `ALIEXPRESS_LIVE_API_ENABLED=true`, credenciais completas e:

```text
DRY_RUN=true
PUBLISH_REAL_DEALS=false
PUBLISH_WITHOUT_AFFILIATE=false
SEARCH_ENABLED=false
```

A configuração de exemplo continua desabilitada. Gate de API e gates de publicação são separados;
DRY_RUN não significa, por si só, ausência de consulta à API. O comando com `--message-id` pode
consultar a API quando não houver prova válida e só deve ser executado após autorização live
separada. O ID é o ID interno do banco, não o ID externo do Telegram. Ele requer schema atualizado;
nenhum upgrade é implícito.

O preview ponta a ponta **offline**, pronto para inspeção, é:

```powershell
uv run promo-bot aliexpress convert-preview --offline-demo
```

Essa opção ignora `.env` e configuração local, usa valores fictícios, mensagem sintética,
`MockTransport` e banco apenas em memória, descartado ao terminar. A saída é identificada como
`synthetic=true` e `evidence_source=MockTransport`, inclui texto convertido e link fictício, e
confirma uma chamada simulada e reutilização no segundo preview. Não siga o link demonstrativo.
É mutuamente exclusiva com `--message-id`.

TLS normal, `trust_env=False` e redirects desativados permanecem no cliente HTTP. Representações
do serviço e do preview ocultam tracking, texto e link; erros retornam códigos locais sanitizados.
O log HTTPX contendo o endpoint assinado é filtrado antes dos handlers e os logs de transporte
HTTPX/HTTPCore ficam em WARNING. Testes unitários desabilitam o carregamento implícito do `.env`;
somente arquivos temporários explicitamente indicados nos testes de configuração são lidos.

Toda a validação desta fase usa mensagens e respostas sintéticas. Nenhuma chamada live AliExpress,
Telegram real, publicação ou alteração do banco operacional foi executada pelo agente nesta fase.

Validação offline em 2026-09-04: `uv sync --locked --offline` conferiu 41 pacotes;
`ruff check .` passou; `ruff format --check .` confirmou 109 arquivos; `mypy src` passou em 62
arquivos-fonte; `pytest` terminou com **274 passed, 5 deselected in 26.58s**. O smoke
`convert-preview --offline-demo` terminou com sucesso, uma chamada simulada, uma substituição,
cache reutilizado no segundo preview e nenhuma entrega. A migration foi verificada somente em
SQLite temporário pelos testes, sem tocar no banco operacional.
