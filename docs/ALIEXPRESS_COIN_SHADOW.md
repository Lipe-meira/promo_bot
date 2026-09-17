---
feature_gate:
  name: ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED
  default: false
accepted_inputs:
  - https://s.click.aliexpress.com/e/_[A-Za-z0-9]{7,8}
  - https://a.aliexpress.com/_[A-Za-z0-9]{7,8}
budgets:
  source_values: 1
  top_calls: 1
  attempts: 1
  retries: 0
  redirects_followed: 0
evidence_fields:
  - tracking_confirmed
  - correlation_mode
  - attribution_unverified
  - route_preservation_manually_observed
retention_hours: 24
production_publication: false
fallback_to_canonical: false
commission_confirmed: false
secret_rotation_invalidates_cache: true
command_examples:
  - - aliexpress
    - coin-shadow-preview
    - --config
    - config.yaml
    - --message-link
    - https://t.me/c/1234567890/77
    - --shadow-database
    - runtime/coin-shadow.sqlite3
  - - aliexpress
    - coin-shadow-auto-deliver
    - --config
    - config.yaml
    - --message-link
    - https://t.me/c/1234567890/77
    - --shadow-database
    - runtime/coin-shadow.sqlite3
    - --destination
    - private-test
---

# AliExpress coin-shadow

Este fluxo experimental existe somente para os formatos comprovados
`https://s.click.aliexpress.com/e/_[A-Za-z0-9]{7,8}` e
`https://a.aliexpress.com/_[A-Za-z0-9]{7,8}`. Ele envia o short original
diretamente como o único `source_value`: não expande, abre, resolve, canonicaliza
ou reconstrói a URL. Também não usa o parser posicional no fluxo canônico, não
interpreta destinos longos em `best.aliexpress.com` e não possui fallback para URL
normal de produto.

O destino interno de um short pode herdar contexto anterior, incluindo campos
como `utm`, `from` e outros identificadores. O coin-shadow não observa, interpreta,
copia, remove nem reconstrói esses valores; eles permanecem opacos dentro do short
literal. Em particular, o fluxo não acrescenta parâmetros como `channel=coin`,
`sourceType` ou `improveDiscount`.

## Limites operacionais

O gate `ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED` começa fechado. Quando uma execução
for explicitamente habilitada, o orçamento permanece fixo em uma entrada, uma
tentativa e no máximo uma chamada `aliexpress.affiliate.link.generate`, sem retry
e sem seguir ou abrir o link retornado. Falha, timeout ou resultado ambíguo termina
em revisão segura ou estado incerto; mensagens posteriores e reinícios não
regeneram esses estados automaticamente.

O preview preserva o texto original e troca somente a ocorrência visível do
short. A saída normal contém apenas metadados sanitizados. Conteúdo pode aparecer
somente no stdout de `coin-shadow-preview --include-content`; nunca em stderr ou
logs estruturados. O auto-delivery nunca imprime o conteúdo completo.

O auto-delivery aceita apenas o alias `private-test` da allowlist existente. O
destino deve ser um canal privado diferente de todos os canais-fonte. Antes do
único `sendMessage`, o adapter confirma a identidade privada do canal, que o bot
é membro administrador/proprietário e que possui permissão de postagem. A reserva
durável de mensagem e destino impede reenvio mesmo depois do purge do preview.

## Significado da evidence

- `tracking_confirmed` significa somente que a API devolveu exatamente o tracking
  configurado.
- `correlation_mode` é `SOURCE_VALUE_EXACT` ou, sob as regras singleton dedicadas,
  `POSITIONAL_SINGLETON`.
- `attribution_unverified` permanece sempre `true`.
- `route_preservation_manually_observed` começa sempre `false`.

Esses campos não confirmam comissão. A correlação posicional e o tracking devolvido
pela API não determinam qual atribuição prevalece quando o short de entrada contém
contexto ou atribuição interna. Nenhuma resposta TOP bruta, short, tracking,
credencial ou secret é persistido.

Evidence `READY`, promotion link validado e conteúdo do preview têm retenção
lógica de 24 horas. Esse prazo é apenas política de cache e purge; não garante a
validade de preço, desconto, moedas ou estoque. A fingerprint usa domínios HMAC
separados derivados do `app_secret`; o secret nunca é persistido e sua rotação
invalida naturalmente o cache anterior.

## Isolamento e validação futura

O fluxo usa tabelas, serviços, comandos e transporte dedicados. Ele não cria
`Deal`, delivery/outbox de produção ou candidato canônico, e não pode publicar em
produção enquanto `attribution_unverified=true`. O resultado funcional observado
em um caso não deve ser generalizado para outros shorts, contas, produtos ou
momentos.

Um teste manual futuro deve usar o mesmo celular, conta, produto e variação para
comparar o short original e o novo link quanto à tela de moedas, preço, desconto e
moedas necessárias, sem finalizar a compra. Mesmo que a rota seja preservada,
atribuição financeira só pode ser estabelecida por pedido controlado e relatório
oficial. Alterar `route_preservation_manually_observed` ou liberar produção exige
uma ação explícita futura do operador; esta implementação não oferece essa ação.
