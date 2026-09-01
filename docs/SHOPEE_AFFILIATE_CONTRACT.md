# Gate do contrato oficial da Shopee Affiliate

## Estado

**BLOQUEADO — contrato autenticado ainda não fornecido ou confirmado de forma sanitizada.**

Não implementar cliente real enquanto todos os itens abaixo não forem confirmados diretamente no
Explorer oficial autenticado da Shopee Affiliate Open API Brasil.

## Evidências necessárias

- endpoint brasileiro;
- algoritmo e bytes exatos da assinatura;
- timestamp e unidade;
- serialização do payload;
- headers obrigatórios;
- queries e mutations atuais;
- campos e nulabilidade;
- semântica de preço, faixa, modelo e variação;
- limites de Sub IDs;
- semântica e escopo de unicidade de `shop_id`, `item_id` e do identificador de produto aceito;
- códigos de erro e classificação retryable/permanente;
- rate limits e comportamento de `Retry-After`;
- hosts oficiais de shortlink;
- hosts oficiais de imagens, se documentados.

## Forma segura de confirmação

Registrar neste documento apenas nomes de operações, campos, regras e exemplos sintéticos. Não
incluir App ID completo, secret, assinatura, Authorization, cookies, tokens, payload real ou resposta
que identifique a conta.

## Consequência do gate

Enquanto o estado for `BLOQUEADO`, o projeto pode conter contratos internos, DTOs, persistência,
retomada, mocks e testes offline. Qualquer adapter de produção deve falhar explicitamente com
`SHOPEE_OFFICIAL_CONTRACT_UNAVAILABLE`; ele nunca pode simular consulta ou shortlink bem-sucedido.
