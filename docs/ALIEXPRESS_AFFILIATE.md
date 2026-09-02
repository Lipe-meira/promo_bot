# AliExpress Affiliate — contrato offline e gate operacional

## Estado deste marco

Este marco implementa somente o que pode ser comprovado por documentação oficial e pelo pacote
sanitizado coletado no AliExpress Open Platform. Não houve chamada real, uso do API Testing Tool,
scraping, navegador, publicação no Telegram ou processamento de pedidos.

O provider permanece desabilitado em `config.example.yaml`. O cliente produtivo retorna
`ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE`; um stub indisponível não simula sucesso.

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

## O que falta para liberar o transporte real

A documentação genérica oficial confirma um gateway REST, parâmetros comuns e uma forma de
HMAC-SHA256. O pacote anexado, porém, usa o protocolo TOP com o nome pontuado da operação no campo
`method`. Ainda falta uma fonte oficial que congele, para essas seis operações Affiliate:

1. o gateway/base URL produtivo exato e sua região;
2. se a chamada é path-based ou `method=<operação>` no corpo/query;
3. a sequência exata de bytes assinados para nomes pontuados, incluindo ou não path/método;
4. quais parâmetros comuns entram na ordenação e na assinatura;
5. a serialização e codificação exatas antes do HMAC;
6. a unidade, fuso e janela válida do timestamp;
7. o transporte final (query, form ou body) dos parâmetros comuns e de negócio.

Até esses pontos serem confirmados conjuntamente por documentação oficial ou código de SDK oficial
aplicável ao mesmo protocolo, não haverá signer produtivo nem tentativa por inferência. A futura
validação live exigirá autorização separada e deverá começar com consulta controlada, sem publicação.
