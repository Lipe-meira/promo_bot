# Shopee Affiliate — Fase 3

## Escopo

A Fase 3 enriquece somente candidatos Shopee já identificados pelo relay. Busca independente,
scraping, Playwright, carrinho, teste de cupom e AliExpress permanecem fora do escopo.

O provider deve usar exclusivamente a Shopee Affiliate Open API Brasil. A biblioteca SaAPI pode ser
consultada como material de auditoria, mas não define assinatura, endpoint, operações ou campos.

## Controles obrigatórios

Durante desenvolvimento e validação offline:

```env
DRY_RUN=true
PUBLISH_REAL_DEALS=false
PUBLISH_WITHOUT_AFFILIATE=false
SEARCH_ENABLED=false
COUPON_BROWSER_VERIFICATION=false
```

Uma oferta real exige simultaneamente provider Shopee habilitado em modo `official_api`, credenciais
válidas, negócio `READY`, prova de link oficial, `DRY_RUN=false`, `PUBLISH_REAL_DEALS=true`, limite
horário disponível e ausência de entrega equivalente.

## Dados e credenciais

As credenciais locais são `SHOPEE_APP_ID` e `SHOPEE_SECRET`. Nunca envie esses valores, códigos,
assinaturas, headers ou payloads autenticados ao Codex, ChatGPT, GitHub ou logs. O arquivo `.env`
real permanece ignorado e não deve ser inspecionado por tarefas de desenvolvimento.

## Validação live

O teste live da Shopee é separado e desabilitado por padrão. Quando futuramente autorizado, poderá
consultar um produto conhecido e gerar um shortlink oficial. Ele não poderá enviar mensagem ao
Telegram, alterar negócio para `SENT`, ativar busca em massa ou habilitar publicação automática.

A publicação live é uma validação posterior, distinta e sujeita a nova autorização explícita.
