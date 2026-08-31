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

O serviço de entrega revalida essas condições imediatamente antes de fazer o claim da outbox. A
trava não depende apenas da CLI. `SENT` requer resposta positiva da Bot API e `message_id`.

Não existe garantia exactly-once: SQLite e Telegram não compartilham transação. Timeout de leitura,
timeout após o início do envio ou lease expirada em `SENDING` produzem `DELIVERY_AMBIGUOUS`, sem
reenvio automático.

## Estado implementado

- máquinas de estado independentes para mensagem, candidato, negócio e entrega;
- migration e retomada de candidatos legados;
- `processed_items` tratado somente como histórico de observação;
- contratos e DTOs independentes do GraphQL;
- BRL obrigatório;
- preço exato separado de faixa “a partir de”;
- variação somente quando identificada e confirmada;
- imagem externa descartada enquanto a allowlist oficial de CDN não estiver confirmada;
- prova afiliada sanitizada, sem assinatura, headers ou resposta integral;
- formatter textual com botão “Abrir oferta”;
- outbox com `DELIVERY_AMBIGUOUS` e sem afirmação de exactly-once.

Ainda não implementado por causa do gate:

- cliente `httpx.AsyncClient` da Shopee;
- assinatura e headers;
- queries, mutations e parsing reais;
- rate limit específico da Shopee;
- hosts oficiais de shortlink e CDN;
- consulta ou geração real de shortlink.

## Dados e credenciais

As credenciais locais são `SHOPEE_APP_ID` e `SHOPEE_SECRET`. Nunca envie esses valores, códigos,
assinaturas, headers ou payloads autenticados ao Codex, ChatGPT, GitHub ou logs. O arquivo `.env`
real permanece ignorado e não deve ser inspecionado por tarefas de desenvolvimento.

## Validação live

O teste live da Shopee é separado e desabilitado por padrão. Quando futuramente autorizado, poderá
consultar um produto conhecido e gerar um shortlink oficial. Ele não poderá enviar mensagem ao
Telegram, alterar negócio para `SENT`, ativar busca em massa ou habilitar publicação automática.

A publicação live é uma validação posterior, distinta e sujeita a nova autorização explícita.

Quando o contrato estiver confirmado e o cliente real existir, a futura validação deverá:

1. manter `PUBLISH_REAL_DEALS=false`;
2. configurar `SHOPEE_APP_ID` e `SHOPEE_SECRET` somente no `.env` local;
3. selecionar um produto conhecido e não sensível;
4. executar somente `tests/live/test_shopee_live.py` mediante autorização explícita;
5. verificar consulta e geração do shortlink em saída sanitizada;
6. confirmar que nenhuma entrega Telegram foi criada.

App ID, secret, assinatura, tokens, cookies e respostas identificáveis nunca devem ser enviados ao
Codex, ChatGPT ou GitHub.
