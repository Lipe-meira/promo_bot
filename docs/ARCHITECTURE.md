# Arquitetura — Fases 1 a 3

## Princípios

- Domínio independente de SQLAlchemy, CLI e integrações externas.
- Segredos somente no ambiente local; comportamento não secreto em YAML validado.
- SQLite evoluído por migrações Alembic que preservam os dados existentes.
- Telethon restrito à leitura dos canais configurados.
- Telegram Bot API separada da sessão de usuário.
- SQLite como fonte de verdade; fila em memória apenas como mecanismo de entrega.
- Toda operação HTTP falha de forma fechada quando sua segurança não pode ser comprovada.
- Candidato sem link afiliado oficial nunca é publicável.
- Ingestão, enriquecimento afiliado, validade comercial e entrega usam estados independentes.
- SQLite e Telegram não formam uma transação atômica; uma entrega ambígua nunca é repetida
  automaticamente.

## Componentes

```text
CLI
├── config ── YAML + ambiente
├── telegram
│   ├── monitor ── Telethon somente leitura
│   └── bot ── envio exclusivamente sintético na Fase 2
├── relay
│   ├── parser ── texto + entidades + botões
│   ├── queue ── persistência antes do enqueue + recovery
│   ├── service ── máquina de estados + deduplicação
│   └── formatter ── templates próprios
├── stores.urls ── identificação e canonicalização
├── security.urls ── DNS, redirect e peer checks
├── providers
│   ├── base ── contrato interno independente de fornecedor
│   └── shopee ── adapter oficial, bloqueado até confirmação documental
├── affiliate ── candidatos, retomada, prova de afiliação e enriquecimento
├── delivery ── outbox e envio protegido
├── database ── SQLAlchemy + Alembic + SQLite
├── observability ── logs JSON sanitizados
└── domain ── tipos e estados sem dependências de infraestrutura
```

## Processamento durável

1. O adaptador extrai todos os links da mensagem.
2. A mensagem e os links estruturados são gravados como `RECEIVED`.
3. O checkpoint do canal avança na mesma transação.
4. Somente depois do commit o ID interno entra na fila limitada.
5. O worker faz claim atômico e muda o estado para `PROCESSING`.
6. Cada link recebe um resultado persistido.
7. O processamento determinístico termina em `COMPLETED`, ainda que o candidato esteja
   `PENDING_AFFILIATE` ou `MANUAL_REVIEW`.

Se a fila estiver cheia, a mensagem permanece `RECEIVED` com `QUEUE_CAPACITY_DEFERRED`. O recovery
retoma `RECEIVED`, `FAILED_RETRYABLE` vencido e `PROCESSING` cuja lease expirou. Tentativas são
limitadas e usam backoff exponencial com jitter.

Somente `COMPLETED` é tratado como duplicata concluída. `FAILED_PERMANENT` não é repetido
automaticamente e divergência de `content_hash` nunca sobrescreve o conteúdo original.

## Catch-up

Ao iniciar, o monitor consulta no máximo a quantidade configurada de mensagens recentes de cada
canal. Aplica janela temporal e checkpoint, ordena da mais antiga para a mais nova e usa a mesma
persistência do tempo real. Depois registra `NewMessage` e faz uma segunda passagem curta para cobrir
a transição. A sobreposição é absorvida pela identidade persistida. Nenhuma operação marca mensagens
como lidas.

## Segurança de URLs

Somente hosts exatos das cinco lojas e seus encurtadores conhecidos podem receber conexão. Cada
salto valida esquema, porta, credenciais embutidas, host, resolução DNS e endereços globais. O cliente
usa `follow_redirects=False` e `trust_env=False`.

Depois da conexão, o IP do peer deve ser global e pertencer ao conjunto validado antes daquele salto.
Se o transporte não expuser o peer, a expansão falha com `PEER_IP_UNVERIFIED`.

Essa combinação reduz o risco de SSRF e DNS rebinding, mas não é descrita como proteção absoluta: há
uma diferença inerente entre consultar DNS e abrir a conexão. Em plataformas nas quais não seja
possível verificar com segurança o destino efetivamente usado, a requisição é recusada em vez de
relaxar a proteção.

## Limite da Fase 2

Não há providers, afiliados, busca independente, consulta de produto, cupom, Playwright ou scheduler.
O relay apenas identifica candidatos. A única escrita externa disponível é o teste sintético e fixo
da Bot API, acionado separadamente pelo operador.

## Fase 3 — Shopee Brasil

A mensagem-fonte termina em `COMPLETED` assim que os links e candidatos são persistidos. Falhas
posteriores não alteram esse estado. O candidato afiliado controla seu próprio claim, lease,
tentativas e backoff. Um negócio `READY` representa somente validade comercial e prova oficial de
afiliação. O resultado do Telegram pertence exclusivamente à entrega.

O fluxo é:

```text
SourceMessage COMPLETED
    → AffiliateCandidate PENDING_AFFILIATE
    → AffiliateCandidate VALIDATING
    → AffiliateCandidate ENRICHED
    → Deal READY
    → Delivery PENDING/SENDING
    → Delivery SENT ou DELIVERY_AMBIGUOUS
```

`SENT` exige resposta positiva da Bot API e `message_id`. Timeout após o início da requisição é
ambíguo porque não existe transação atômica entre SQLite e Telegram. `DELIVERY_AMBIGUOUS` exige
revisão humana e nunca retorna automaticamente para a fila.

O cliente real da Shopee só pode existir depois da confirmação sanitizada do contrato no Explorer
oficial autenticado. Até esse gate, adapters explícitos retornam indisponibilidade e os testes usam
fakes offline; nenhum stub simula sucesso real.

## Fase 6 — Mercado Livre, primeiro marco offline

O candidato Mercado Livre reutiliza `affiliate_candidates` como fila durável. Após uma futura
validação oficial de catálogo, ele poderá permanecer em `AWAITING_AFFILIATE_GENERATION` sem manter
uma lease. A transição para `GENERATING_AFFILIATE` pertence ao gate do navegador real e não é
executada neste marco.

Revisão e transporte são responsabilidades separadas. `deals.review_state` representa aprovação
humana e eventual divulgação; `deliveries.purpose` diferencia `INTERNAL_REVIEW` de
`EXTERNAL_DISCLOSURE`. Um `SENT` para o chat operacional significa somente que a cópia de revisão
foi entregue, nunca que houve divulgação pública.

Não há novas tabelas de worker. A lease existente protege o candidato e um lock exclusivo de
arquivo/processo protege o perfil. Estado persistente próprio para pausa ou circuit breaker será
avaliado somente quando o worker contínuo for autorizado.

O adaptador de interface recebe um contrato explícito de seletores. O contrato incluído é somente
de fixture local; o contrato oficial permanece ausente e falha antes da navegação. Consulte
`docs/MERCADO_LIVRE_AFFILIATE.md` para os gates contratuais e operacionais.

## Fase 4 — AliExpress, marco offline

AliExpress reutiliza a fila durável `affiliate_candidates`, as provas afiliadas, produtos, negócios
e histórico já existentes. A única tabela específica é `aliexpress_product_snapshots`, porque o
contrato precisa preservar SKU, escopo/faixa de preço, comissão, frete e operação-fonte sem perder
semântica no modelo compartilhado.

DTOs, payloads e parsers são independentes do transporte. Somente `link.generate` pode produzir a
prova ligada ao `source_value` solicitado; respostas de produto nunca provam afiliação. A criação de
`READY` e a transição do candidato acontecem em uma transação protegida pela lease. Nenhuma outbox
é criada por esse enriquecimento offline.

O transporte HTTP reutilizável recebe endpoint e signer por injeção, desabilita redirects e
variáveis de proxy do ambiente e limita retries. A composição produtiva desses componentes continua
ausente e falha com `ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE`, pois o contrato TOP completo
para métodos Affiliate pontuados ainda não foi confirmado.
