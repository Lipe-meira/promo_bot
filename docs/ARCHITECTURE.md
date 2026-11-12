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
  `UNVERSIONED` (tem tabelas sem `alembic_version`), `UNREADABLE` (não é SQLite, erro de acesso ou
  journal de rollback presente, que um escritor desfaria) e `ORPHAN_SIDECARS` (existem `-wal`, `-shm`
  ou `-journal` e não existe o arquivo principal).
- Sidecars órfãos (M-01): são evidência, nunca são adotados nem apagados. `listen` e `init-db` recusam
  com `RUNTIME_DB_ORPHAN_SIDECARS` sem criar o banco. Antes da correção, criar um banco novo sobre um
  `-wal` órfão com uma linha confirmada **apagou** esse `-wal` (reproduzido com banco temporário).
  Mova-os, juntos, para uma quarentena (não apague).
- `listen` só **cria** banco novo (`MISSING`/`EMPTY`, comportamento anterior) ou exige `CURRENT`, caso em
  que o Alembic nem é aberto e nada é escrito. Qualquer outro estado levanta `RuntimeMigrationError`
  (código estável, sem caminhos) antes de construir o listener; o comando sai com código 2 e orienta
  executar `init-db`. Falha ao criar banco novo remove o arquivo recém-criado. O comando mantém o lock
  de listener (abaixo) durante toda a vida do monitor.
- `init-db` é a única via de upgrade de banco existente: adquire os dois locks do SO (abaixo), inspeciona
  já sob os locks, faz o backup verificado e só então a migration. Com listener ativo recusa com
  `RUNTIME_DB_LISTENER_ACTIVE` antes de inspecionar ou escrever. Estados
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

### Locks entre processos (F1/F3)

- Dois locks cooperativos do sistema operacional (`msvcrt.locking` no Windows, `flock` nos demais), no
  padrão do `ShadowListenerLock`, mas independentes dele, em arquivos marcadores de 1 byte ao lado do
  banco: `<banco>.migrate.lock` e `<banco>.runtime-listener.lock`. Vale o lock do SO, não a existência
  do arquivo; os marcadores permanecem em disco e não precisam ser removidos.
- Ordem fixa e sem bloqueio: primeiro o de migração, depois o de listener. `listen` pega os dois, prepara
  o banco (cria se ausente, senão exige `CURRENT`), solta o de migração e **mantém o de listener durante
  todo o monitor**. `init-db` mantém os dois durante todo o upgrade. Como o listener adquire o seu lock
  antes de soltar o de migração, não há intervalo desprotegido entre a preparação e o monitor.
- Resultados: `init-db` com listener ativo e um segundo `listen` no mesmo banco falham com
  `RUNTIME_DB_LISTENER_ACTIVE`; `listen` durante um `init-db` (ou durante a preparação de outro listener)
  falha com `RUNTIME_DB_MIGRATION_IN_PROGRESS`; erro ao abrir ou travar o marcador falha com
  `RUNTIME_DB_LOCK_FAILED`. Tudo antes de construir o monitor ou de tocar no banco.
- Encerramento forçado: o SO libera o lock do processo morto (medido no Windows: no máximo 6 ms depois
  do fim do processo, em 25 execuções); não existe lock órfão nem remoção manual. Um `init-db` morto pode
  deixar um `*.bak.partial`, que nunca é promovido a backup válido e pode ser apagado pelo operador.
- Limites: o lock é cooperativo (o shell `sqlite3` e outras ferramentas o ignoram), vale numa só máquina
  (não protege volumes de rede compartilhados entre computadores) e cada arquivo de banco tem o seu par de
  locks. O comportamento sob falha de energia não foi exercitado.

### Identidade única do banco (H-02) e hard links (H-03)

- **Uma só identidade.** O caminho é resolvido uma vez (`expanduser` + `resolve`) e a URL canônica é
  derivada dele e conferida na ida e volta (se apontar para outro arquivo, por exemplo por um `?` no
  caminho, `RUNTIME_DB_URL_UNSUPPORTED`). Inspeção, locks, backup, migration (a engine do Alembic) e a
  conexão operacional do listener (`session.database_url`) usam exatamente essa identidade. Antes, uma URL
  com `~` fazia o módulo proteger e copiar `<home>\x.sqlite3` enquanto a engine migrava `./~/x.sqlite3`: dois
  bancos diferentes (reproduzido com dois bancos sintéticos; agora o não protegido permanece intacto).
- **Hard links.** Os nomes dos locks derivam do caminho; outro *nome* do mesmo arquivo teria outro par de
  locks e contornaria a exclusão (reproduzido entre processos no Windows, em DELETE e WAL), e o SQLite
  mantém journals por nome, o que também é inseguro para o próprio banco. Um arquivo com `st_nlink != 1`
  é recusado com `RUNTIME_DB_ALIAS_UNSUPPORTED`, na entrada de `listen` e `init-db` (já sob os locks) e de
  novo imediatamente antes da primeira escrita. Basta remover o link extra para voltar a usar o banco.
- **Limites.** O lock não é por inode: um link criado por outro ator entre a última checagem e a escrita
  não é impedido. Symlinks e junctions são resolvidos por `resolve()` para o mesmo caminho; outros aliases
  de volume (por exemplo letras de unidade mapeadas) não foram exercitados.

### Erros controlados

Nenhuma falha esperada mostra traceback, caminho local ou conteúdo do banco: a leitura da revisão que
falha vira `RUNTIME_DB_UNREADABLE`; erros de tamanho, espaço ou promoção do backup viram
`RUNTIME_DB_BACKUP_FAILED`; erro de lock vira `RUNTIME_DB_LOCK_FAILED`; qualquer outro `OSError` ou
`sqlite3.Error` inesperado vira `RUNTIME_DB_STORAGE_ERROR`. O `main` imprime só o código e a orientação.

Liberação dos locks (M-02): falha ao desbloquear é inofensiva (fechar o arquivo libera o lock). Falha ao
fechar o arquivo vira `RUNTIME_DB_LOCK_RELEASE_FAILED`, com nova tentativa de fechar para não deixar um
arquivo vazado segurando o lock. Esse erro **nunca mascara** um erro que já estava em andamento (a falha do
monitor, ou o motivo real de um `init-db` recusado); quando é o único erro, o trabalho já terminou e a
mensagem diz para conferir se há processo preso ao banco.

### Recuperação manual do banco runtime

O bot **nunca** restaura sozinho; o procedimento abaixo é do operador e não deve ser executado em um
banco em uso.

1. Pare todos os processos que usam o banco: o listener, qualquer `init-db` e qualquer ferramenta que
   abra o mesmo arquivo. Os locks só cobrem processos cooperativos, então confira também por fora.
2. Escolha apenas um backup `*.bak` completo criado pelo `init-db` (nunca um `*.partial`) e verifique-o
   antes, em modo somente leitura: `PRAGMA integrity_check` deve devolver `ok` e `alembic_version` deve
   ser a revisão que aparece no nome do arquivo.
3. Crie uma pasta de **quarentena** fora do diretório do banco e **mova** para ela, sem apagar nem
   sobrescrever, o banco e todos os seus sidecars existentes (`<banco>`, `<banco>-wal`, `<banco>-shm` e
   `<banco>-journal`), mantendo-os juntos. O `-wal` e o `-journal` podem conter transações confirmadas
   depois do backup e só têm sentido ao lado do arquivo a que pertencem. Sidecars sem o arquivo principal
   (`RUNTIME_DB_ORPHAN_SIDECARS`) também vão para a quarentena, nunca para a lixeira.
4. **Copie** (não mova) o backup verificado para o caminho original do banco, mantendo o `.bak`. Nenhum
   `-wal`, `-shm` ou `-journal` antigo pode ficar ao lado do arquivo restaurado: o SQLite pode reaplicar
   sidecars velhos e produzir um banco híbrido, sem erro (reproduzido na auditoria com bancos temporários).
5. Rode `promo-bot init-db` (que faz novo backup se houver migration pendente) e confira o resultado antes
   de iniciar o listener.
6. Mantenha a quarentena até terminar a investigação.

Alterações feitas depois do backup **não** estarão no banco restaurado: o backup é um instantâneo do
início do upgrade, e o que foi gravado depois (inclusive durante o backup ou a migration por processos que
ignoram os locks) existe apenas nos arquivos da quarentena. O bot não promete nem implementa a
recuperação desses commits. Uma eventual recuperação deve trabalhar só em **cópias** da quarentena, e
convém avaliar antes de reiniciar o impacto de mensagens e entregas registradas após o backup que deixaram
de existir (reprocessamento ou envio repetido).

### Garantias e limites conhecidos

Nada disso aplica um backup a bancos novos; o espaço em disco é verificado de forma estimada;
`mode=ro` pode criar `-shm` em banco WAL fechado, sem alterar o conteúdo. Os bancos shadow
(`upgrade_database_async` em `cli.py`, `discovery/runtime.py` e `discovery/sku_runtime.py`) **não**
passam por essa política e continuam migrando implicitamente. A migração atômica real (DDL
transacional via `BEGIN` explícito no driver) foi avaliada e não adotada: afetaria todas as migrations
e os bancos shadow e exigiria validação própria.

Pontos registrados e **não** corrigidos (sem necessidade técnica comprovada para esta tarefa):

- F5, journal transitório: `inspect_schema` trata qualquer `-journal` presente como `UNREADABLE`. Com
  escritores que não cooperam com os locks e estão ativos, isso produz recusas espúrias (falha fechada,
  medido em 11 de 18 tentativas no cenário da auditoria). Como `listen` e `init-db` agora se excluem,
  o caso só ocorre com ferramentas externas.
- F6, esquema inspecionado só pela revisão: `CURRENT` significa `alembic_version == head`; uma tabela
  removida de um banco no head não é detectada (reproduzido na auditoria). Não é regressão da T5A.

### Integridade física do banco CURRENT (M-03)

- **O que `CURRENT` comprova:** o arquivo abre em modo somente leitura, `sqlite_master` é legível e há
  uma única linha em `alembic_version` igual ao head. Só isso.
- **O que não comprova:** `listen` **não** executa `integrity_check` nem `quick_check` e não confere
  tabelas, índices ou páginas. Um banco com a versão certa e páginas corrompidas fora das lidas passa
  como `CURRENT` e o problema só aparece no uso (caracterizado em teste).
- **O que protege hoje:** o `init-db` de um banco com migration pendente só migra depois de um backup
  cujo `PRAGMA integrity_check` é `ok`, de modo que corrupção estrutural detectada pelo SQLite recusa o
  upgrade sem alterar o arquivo (testado). Isso não vale para um banco já `CURRENT`.
- **Decisão:** não verificar na inicialização, porque o custo cresce com o tamanho do banco e atrasa o
  start, e porque a verificação não prova consistência lógica. Um `quick_check` opcional pode ser
  avaliado em tarefa própria. Operacionalmente, com o listener parado, rode `PRAGMA integrity_check`
  numa **cópia** do banco antes de operações de risco e depois de queda de energia ou disco cheio.

### Artefatos fora do Git (L-01)

O `.gitignore` cobre `*.sqlite3-wal`, `*.sqlite3-shm`, `*.sqlite3-journal`, os backups `*.pre-*.bak`, os
parciais `*.bak.partial` e os marcadores `*.migrate.lock` e `*.runtime-listener.lock`, com padrões
específicos que não atingem `uv.lock` (testado com `git check-ignore`).

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
