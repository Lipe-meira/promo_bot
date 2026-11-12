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

Workers e loop de recuperação são supervisionados: uma exceção inesperada (por exemplo, erro do
SQLite ao reivindicar, falhar ou concluir uma mensagem) é registrada apenas com código estável e
tipo da exceção, o item é liberado e o loop continua após um backoff exponencial com teto de 30 s,
que volta ao início depois de um sucesso. A mensagem nunca é marcada como processada por essa
falha: o estado durável (`RECEIVED`, `FAILED_RETRYABLE` ou lease de `PROCESSING` vencida) é o que a
recuperação consulta, respeitando limites de tentativas e backoff. Passos independentes da
recuperação (backfill de candidatos e retomada de mensagens) falham isoladamente.

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

Antes de qualquer parsing, resolução DNS ou requisição, a URL inicial e cada cabeçalho `Location`
têm os caracteres brutos verificados: controles, espaços, separadores Unicode e caracteres
invisíveis (como bidi e largura zero) são rejeitados com `INVALID_URL` ou `INVALID_REDIRECT_URL`,
pois `urlsplit` e `urljoin` descartam tabs e quebras de linha em silêncio. Hosts não ASCII, a porta 0
e URLs acima de 4.096 caracteres também são rejeitados. Letras não ASCII em caminhos continuam
permitidas, porque redirecionamentos reais de lojas podem trazê-las. Links devolvidos por APIs
oficiais seguem a validação em camadas de `security/link_validation.py` (ver
[contrato AliExpress](ALIEXPRESS_AFFILIATE.md)).

Links AliExpress já persistidos são revalidados com o mesmo contrato imediatamente antes do uso, porque linhas gravadas antes do endurecimento podem estar malformadas: a entrega shadow (link único e multi-link, `SHADOW_LINK_INVALID`), `AliExpressEnrichmentService` e a validação do histórico para provas canônicas e evidências coin-shadow (`AFFILIATE_HISTORY_LINK_INVALID`) rejeitam o link sem tocar no transporte. A rejeição só devolve um código fixo: não regenera, reescreve nem reenvia o link, não altera provas gravadas e não afeta a deduplicação nem os estados duráveis da entrega.

Depois da conexão, o IP do peer deve ser global e pertencer ao conjunto validado antes daquele salto.
Se o transporte não expuser o peer, a expansão falha com `PEER_IP_UNVERIFIED`.

Essa combinação reduz o risco de SSRF e DNS rebinding, mas não é descrita como proteção absoluta: há
uma diferença inerente entre consultar DNS e abrir a conexão. Em plataformas nas quais não seja
possível verificar com segurança o destino efetivamente usado, a requisição é recusada em vez de
relaxar a proteção.

## Migrations do banco runtime

Causa-raiz (F-07): `listen` chamava `upgrade_database()` a cada inicialização, então atualizar o
código e reiniciar o listener alterava o banco existente sem aviso nem backup. Reproduzido com bancos
temporários: uma migration que falha no meio deixa o arquivo **meio migrado** (tabelas criadas e
tabela temporária `_alembic_tmp_*` gravadas, `alembic_version` ainda antigo), porque o DDL do SQLite
não é atômico com a configuração atual do driver; uma nova tentativa falha com "table already exists".
As migrations `3d5a7c9e214b`, `5f2c8a1d740e` e `7a4d1e9c82f6` reconstroem tabelas (`batch_alter_table`)
e retropreenchem dados, portanto não são inofensivas em bancos com conteúdo.

Política (`src/promo_bot/database/runtime_migration.py`):

- `inspect_schema` classifica o arquivo **somente leitura** (`mode=ro`), sem criar, migrar nem recuperar:
  `MISSING`, `EMPTY`, `CURRENT`, `PENDING` (revisão conhecida e anterior ao head), `UNKNOWN_REVISION`,
  `UNVERSIONED` (tem tabelas sem `alembic_version`) e `UNREADABLE` (não é SQLite, erro de acesso ou
  journal de rollback presente, que um escritor desfaria).
- `listen` só **cria** banco novo (`MISSING`/`EMPTY`, comportamento anterior) ou exige `CURRENT`, caso em
  que o Alembic nem é aberto e nada é escrito. Qualquer outro estado levanta `RuntimeMigrationError`
  (código estável, sem caminhos) antes de construir o listener; o comando sai com código 2 e orienta
  executar `init-db`. Falha ao criar banco novo remove o arquivo recém-criado.
- `init-db` é a única via de upgrade de banco existente: lock exclusivo `<banco>.migrate.lock` (criação
  `O_EXCL`), nova inspeção sob o lock, backup verificado e só então a migration. Estados
  `UNKNOWN_REVISION`, `UNVERSIONED` e `UNREADABLE` são recusados sem modificar o arquivo. Banco já atual
  retorna `status: current`; banco novo não gera backup.
- Backup: cópia consistente pela API de backup do SQLite (correta com WAL, ao contrário de copiar o
  arquivo), gravada como `<banco>.pre-<revisão>-<UTC>.bak.partial`, verificada (`PRAGMA integrity_check`,
  revisão igual à esperada, nenhum sidecar `-wal`/`-shm`/`-journal`, `fsync`) e só então renomeada para
  `.bak`. Um `.partial` nunca é válido e é removido em qualquer falha, inclusive interrupção. Antes de
  copiar exige-se espaço livre de 2× o tamanho do banco (+WAL) mais 1 MiB. Se outro processo concluir o
  upgrade durante a cópia, a cópia (já atual) é descartada em vez de rotulada como pré-upgrade.
- Falha na migration após o backup: o banco fica bloqueado para o `listen` (estado meio migrado é
  `PENDING`/`UNVERSIONED`/`UNKNOWN`), o backup verificado é mantido e a mensagem cita só o nome do
  arquivo. Não há restauração automática: sobrescrever o banco é decisão do operador.

Recuperação manual: pare listener e `init-db`; confirme que nenhum processo usa o banco; copie o `.bak`
escolhido por cima do banco, apague `-wal`, `-shm` e `-journal` remanescentes do banco antigo e rode
`promo-bot init-db` novamente. Um `.migrate.lock` órfão (processo morto) deve ser removido manualmente
depois de conferir que nenhum `init-db` está em execução.

Garantias e limites: nada disso aplica um backup a bancos novos nem protege contra escritores que
ignoram o lock (o lock é cooperativo entre `listen` e `init-db`); espaço em disco é verificado de forma
estimada; `mode=ro` pode criar `-shm` em banco WAL fechado, sem alterar o conteúdo. Os bancos shadow
(`upgrade_database_async` em `cli.py`, `discovery/runtime.py` e `discovery/sku_runtime.py`) **não**
passam por essa política e continuam migrando implicitamente. A migração atômica real (DDL
transacional via `BEGIN` explícito no driver) foi avaliada e não adotada: afetaria todas as migrations
e os bancos shadow e exigiria validação própria.

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

`SENDING` só nasce em `DeliveryRepository.claim`, que grava `lease_until` na mesma atualização. Se o
dono não fecha a tentativa (processo encerrado, tarefa cancelada, erro de transporte fora das
classes conhecidas, falha ao gravar o resultado depois que o Telegram aceitou), a lease vence: `claim`
já não escolhe a linha, então não há reenvio, e `_finish` recusa o fechamento tardio. Sem
varredura, a linha ficava em `SENDING` para sempre, sem rótulo. `DealDeliveryService.recover_stale_sending()`
(que chama `mark_stale_sending_ambiguous`) converte `SENDING` com `lease_until <= agora` em
`DELIVERY_AMBIGUOUS` com o código `DELIVERY_LEASE_EXPIRED_AMBIGUOUS` e é executado antes de cada
`send()`. A atualização é condicional (estado `SENDING` e lease vencida), então chamadas repetidas ou
concorrentes são seguras; `attempt_count` e `last_attempt_at` são preservados, nenhum `SENT` é
inventado e as demais linhas não são tocadas. Falha definitiva (`FAILED_PERMANENT`) segue distinta de
resultado desconhecido (`DELIVERY_AMBIGUOUS`). O limite inclusivo é o complemento exato do fechamento
(`lease_until > agora`), logo nunca há linha fechável e varrível ao mesmo tempo.

**Limite operacional atual.** A recuperação foi implementada em `DealDeliveryService`, mas hoje esse
serviço não tem chamador no fluxo operacional (só testes o instanciam; `TelegramDealTransport` também
não é montado por nenhum comando). Não existe worker periódico de outbox, e esta tarefa não criou um.
Portanto a recuperação automática em produção depende da integração futura desse serviço: o worker que
vier a usá-lo deverá chamar `recover_stale_sending()` periodicamente, além da chamada que `send()` já
faz antes de cada envio. Os fluxos shadow existentes (`shadow_deliveries` e coin-shadow) não foram
modificados: neles `sending` já vale `uncertain` por contrato e bloqueia reenvio
([entrega shadow](SHADOW_DELIVERY.md)).

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
variáveis de proxy do ambiente e limita retries. O módulo offline `providers.aliexpress.top` é uma
fronteira separada: ele confirma a composição canônica HMAC-SHA256 e produz uma requisição relativa
imutável com `POST /sync`, query comum e formulário de negócio. A query usa pares ordenados para
preservar as duas ocorrências de `method` observadas no SDK Java; essa duplicação é compatibilidade
observada, não contrato normativo.

O módulo offline não implementa o protocolo de signer do transporte, não é importado pelo cliente
ou pelo transporte. A documentação oficial confirma `https://api-sg.aliexpress.com` como
`serverUrl` para `Protocol.TOP`; combinada com `TopExecutor`, que acrescenta `/sync`, ela comprova
`https://api-sg.aliexpress.com/sync` como endpoint TOP. A URL completa é uma composição de duas
fontes oficiais, não uma string exibida literalmente no exemplo da documentação.

A composição produtiva continua ausente e falha com
`ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE` até uma fase de implementação aprovada conectar
o request builder ao transporte. A ordenação física determinística da query e do formulário é
apenas uma decisão da implementação Python; a ordem confirmada pelo SDK é somente a composição
canônica da assinatura.
