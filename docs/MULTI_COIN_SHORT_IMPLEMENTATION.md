# Implementação: múltiplos shorts no listener shadow

Base local examinada: `8f0da4a22b1f9e21dd5b93bb0599c55077d25f0b`.
Branch isolada: `codex/multi-coin-short-shadow`. As verificações originais abaixo
foram offline. A seção de admissão distingue o piloto parcial anterior e sua
correção; a seção de piloto completo registra os cinco cenários concluídos
posteriormente no SHA `5fd54c4`, sem generalizar para operação contínua.

## Contrato entregue

- Opt-in `--allow-multiple-coin-shorts`, mantendo o padrão de uma URL.
- De uma a três ocorrências, conforme `--max-links-per-message`; todas devem ser
  shorts estritos quando houver mais de uma. O canônico isolado continua aceito.
- Mensagem inteira elegível antes de TOP; spans URL de Telegram conferidos em
  UTF-16 e convertidos para índices Python. Sem resolvedor, GET ou fallback.
- Uma chamada singleton por literal distinto sem cache, com tracking exato e
  parser existentes. Orçamento compartilhado; misses conhecidos acima do saldo
  recusam a mensagem antes de criar claims de geração.
- Texto fora dos spans preservado; repetição literal compartilha geração. Preview
  completo somente após todas as validações e limite final de 4096 unidades UTF-16.
- Um único envio privado, reserva durável por mensagem/destino e histórico anterior
  à rede. Falha de persistência impede envio; resultado incerto não autoriza retry.
- PREVIEW/SEND associam gerações distintas com cache por associação e mapa durável
  de todas as ocorrências. Purge não remove reservas nem fatos históricos.
- TTL, bloqueios de legado/contexto de chave e `UNCERTAIN` preservados. Recuperação
  de estado não retoma mensagens antigas nem reabre reservas.

Migration aditiva: `b8c2e4f6a901`, sobre `9b3d5e7f1a20`. Duas tabelas operacionais
de preview/ocorrências e ponteiro opcional na reserva existente. Nenhuma tabela de
histórico adicional, confirmação retroativa ou preenchimento de dados anteriores.
Downgrade recusa dados do novo caminho, inclusive usos duráveis após purge.

## TDD e revisão

Os cinco ciclos observaram RED antes da implementação e GREEN nos testes focados:
contrato; schema/reserva/histórico; cache/orçamento; preview/envio; entrypoint/docs.
Regressões adicionais cobrem o singleton no opt-in com orçamento esgotado e
contagem de trabalho confirmado antes de falha de preview/persistência.

A revisão independente do diff completo encontrou uma incompatibilidade de
admissão de schema: a versão anterior era aceita na abertura, mas a reserva/purge
já exigiam a migration nova. A correção tem regressões RED → GREEN para startup
sem e com opt-in, com transportes proibidos, preservando a consulta read-only.

**Pré-requisito operacional explícito:** todo caminho real de geração/entrega
exige schema `b8c2e4f6a901`, inclusive invocações singleton antigas. Quando a
revisão puder ser lida com segurança, banco anterior é recusado com
`AFFILIATE_HISTORY_SCHEMA_REQUIRED` antes dos transportes, sem migration
automática. Seus comandos continuam iguais após upgrade explícito ou
seleção de banco novo migrado. Auditoria read-only continua disponível no banco
anterior. Não foi implementado ORM paralelo para duas versões de schema.

## Correção P2: admissão de schema sem checkpoint

A abertura gravável anterior podia incorporar commits do WAL ao arquivo principal
e remover o WAL no encerramento, mesmo recusando o schema e não iniciando nenhum
transporte. A regressão RED reproduziu `main_identical=false` e
`wal_identical=false` em singleton e multi, após a recusa sanitizada.

O validador compartilhado decide o schema antes da primeira sessão gravável e
dos transportes. Com `-wal` e `-shm` regulares presentes, usa SQLite `mode=ro`,
`query_only=ON`, timeout de lock zero e transação de leitura. Assim, incorpora a
visão dos commits no WAL; **nunca usa `immutable` ou `nolock` no original**. A
comparação física ocorre antes de limpeza ou de outra conexão de inspeção:
schema recusado preserva os bytes do arquivo principal e do WAL existente.
Migration explícita confirmada somente no WAL é reconhecida corretamente.

O `-shm` é estado de coordenação, não histórico: SQLite pode atualizar read marks
ou reconstruir seu índice existente durante a leitura. Não há garantia de
preservação byte a byte desse sidecar; isso é coberto por teste com índice stale.
Quando **ambos** os sidecars estão ausentes, o fechamento normal de WAL deixa de
ser motivo de recusa. Abrir o original com `mode=ro` poderia criar um WAL vazio;
por isso, somente nesse estado, lê-se o arquivo principal por handle binário e
consulta-se uma cópia privada temporária com `mode=ro&immutable=1`. A cópia foi
completamente gravada e fechada antes da consulta, não tem outro escritor e é
removida junto de seu diretório próprio ao finalizar. Não é um backup. O original
não é aberto por SQLite nessa etapa, nem checkpointado, apagado ou convertido
para DELETE. Seu header escolhe o caminho; tabelas/revisão são consultadas por
SQLite na cópia, não inferidas pelo header.

Verificam-se identidade do handle e do caminho, tamanho/timestamps, ausência dos
sidecars e digest antes/depois da consulta. Mudança detectada gera
`AFFILIATE_HISTORY_STORAGE_UNSTABLE`. Essas verificações são **detecção**, não
snapshot atômico nem garantia de exclusão de escritores externos. Não há suporte
a escritas externas, migration ou substituição concorrentes durante a
pré-checagem; mudanças que ocorram inteiramente entre observações podem não ser
detectadas. O lock existente coordena somente listeners participantes no mesmo
banco, não ferramentas SQLite externas ou todos os comandos manuais.

Sidecars incompletos, não regulares, simbólicos ou incompatíveis com o header do
arquivo principal (WAL presente com header não-WAL) continuam recusados com
`AFFILIATE_HISTORY_WAL_UNVERIFIABLE`, sem criar/recriar sidecars. Um `-journal`
existente (mesmo vazio) causa `AFFILIATE_HISTORY_RECOVERY_REQUIRED`, evitando
ignorar possível recuperação pendente. Banco ausente não é criado. Falha de
leitura/lock retorna `AFFILIATE_HISTORY_STORAGE_UNAVAILABLE`, sem fallback ou
manutenção automática. Nenhum WAL existente é ignorado em favor da cópia.

O lock do listener continua obrigatório; não escrever/migrar/substituir o banco
por processos externos concorrentemente à pré-checagem.
Para um banco admitido, permanece a validação gravável completa de arquivo,
revisão, durabilidade, `synchronous=FULL`, integridade e FKs antes dos transportes.
O fechamento de um banco **admitido** pode executar checkpoint normal. Tracking,
gates, contratos, claims, cache, orçamento e reservas não foram alterados.

## Verificação anterior da correção P2

Resultados do HEAD `c9db98316bd65a87f03327c8e50bbfeb5ec68a77`. Não são validação
da correção operacional de fechamento/reinício registrada abaixo.

- RED: singleton/multi recusam schema anterior sem transportes, mas alteram o
  arquivo principal e removem o WAL. RED adicional: leitura em modo WAL fechado
  cria WAL vazio; código do comando legado também foi preservado por regressão.
- GREEN: 21 casos novos, incluindo comparação física antes de cleanup, schema
  anterior com/sem WAL, banco ausente/inválido, migration confirmada apenas no
  WAL, `-shm` desatualizado/ausente, schema atual e upgrade temporário explícito.
- 54 testes focados passaram. A primeira suíte completa revelou dois erros no
  setup do bypass de caminho temporário do comando legado (alias importado).
  Somente a fixture foi corrigida; 26 testes passaram com importação do comando
  legado primeiro. A suíte completa foi então repetida sobre a árvore corrigida.
- Suíte final: **1028 aprovados, 5 live/browser excluídos, 6 warnings preexistentes**,
  em 327,92 segundos. Settings sintéticas sem `.env`, sockets externos bloqueados,
  transportes falsos e migrations somente em arquivos temporários.
- Lockfile offline (41 pacotes), Ruff format/check (228 arquivos), mypy
  (100 arquivos fonte) e `git diff --check` aprovados. Testes de migration incluem
  upgrade → downgrade → upgrade → check, preservação anterior e recusa não vazia.
- Nenhuma migration nova. Os seis warnings são as quatro ocorrências do ciclo
  Alembic/SQLAlchemy e as duas depreciações datetime descritas abaixo, sem mudança.

O P2 de preservação física foi corrigido naquele HEAD. A investigação operacional
posterior identificou que o fechamento normal de um banco WAL admitido removia
sidecars e provocava recusa no próximo startup. A guarda anterior foi substituída
pelo caminho de cópia privada descrito acima; não é autorização para manutenção
automática de bancos. Piloto real e pendência documental anterior permanecem
separados.

## Correção operacional: fechamento normal e reinício WAL

- RED: seis regressões pelos entrypoints reais, singleton e multi, reproduziram
  recusa de schema atual fechado em WAL, upgrade explícito em WAL e admissão com
  commits no WAL seguida de fechamento/reinício. Sem conexão auxiliar, remoção
  manual de sidecars, transportes ou alteração física durante a recusa.
- Testes permanentes usam subprocessos com ambiente mínimo, settings sintéticas,
  arquivo `.env` desabilitado, DNS/sockets externos bloqueados e adapters falsos.
  Exercitam `init-db`, admissão real de `shadow-auto-deliver` e encerramento normal
  de todas as conexões em cada processo, para singleton e multi.
- Controles cobrem criação e upgrade em DELETE; WAL atual sem sidecars; leitura
  de revisão apenas no WAL; bancos antigos, ausentes ou inválidos; sidecars
  incompletos e journal de recuperação; mudanças externas simuladas sem escrita
  adicional do bot; cleanup somente da cópia privada. Preservação dos bytes é
  exigida nas recusas; um banco admitido pode executar checkpoint normal.
- Tracking, contratos, limites, cache, claims, reservas, entrega e migration não
  mudam. A validação completa de durabilidade, integridade e FKs permanece depois
  da admissão do schema.
- GREEN final: **55 testes focados aprovados**, em 114,59 segundos, incluindo a
  recusa do estado ambíguo de header não-WAL com WAL/SHM presentes antes de abrir
  SQLite. Revisão independente não identificou bloqueador na correção.
- Suíte final do código corrigido: **1062 aprovados, 5 live/browser excluídos,
  6 warnings preexistentes**, em 437,27 segundos. Não reutiliza os resultados de
  `c9db983` como validação. Uma execução intermediária foi interrompida após a
  guarda adicional de estado ambíguo; somente a execução completa da árvore final
  é contabilizada aqui.
- Lockfile offline (41 pacotes), Ruff format/check (231 arquivos), mypy
  (100 arquivos fonte) e diff check aprovados. A suíte inclui os ciclos Alembic
  upgrade → downgrade → upgrade → check em bancos temporários e recusa não vazia.
- Os seis warnings continuam sendo quatro avisos de ciclo de FKs na autogeração
  Alembic e duas depreciações datetime, descritos abaixo. Não foram alterados nem
  corrigidos nesta tarefa.
- Correção mantida em commit local, sem push, merge ou alteração do Draft do
  PR #6. Nenhum banco real, credencial, chamada live ou Telegram foi acessado.
  Piloto real e pendência documental anterior permanecem separados.

## Correção de admissão: criação anterior à janela

Correção posterior ao HEAD `2acc9b0e16d30450f04a5411c0531ce6bfc17d46`.
O piloto parcial foi interrompido; não foi repetido nesta correção offline.

### Evidências e limite da reconstrução

- Início do processo, pelo wrapper: `2026-10-14T13:45:39.218686Z`.
- Conexão TCP: `13:45:43.042990Z`; log de listener conectado:
  `13:45:44.142454Z`. Antes da correção, a admissão era aberta logo após esse
  log, sem await, mas seu instante exato não era armazenado no banco.
- Mensagem 100: `occurred_at=13:37:50Z`, primeira persistência
  `13:48:13.746621Z`; log emitido após persistir, `13:48:13.795349Z`.
- Mensagem 101: `occurred_at=13:49:22Z`, primeira persistência
  `13:48:13.752132Z`; log após persistência, `13:48:13.836543Z`.
- `occurred_at` vem de `message.date`, não de `fwd_from.date`. O adapter não
  preserva data de encaminhamento, instante bruto de chegada ou update bruto.
  Datas SQLite sem offset são UTC pelo contrato de `UTCDateTime`; Brasília é
  UTC-03:00 nesta data (13:37:50Z corresponde a 10:37:50).
- Os fatos duráveis continuam sendo três gerações confirmadas, dois PREVIEW,
  dois SEND confirmados e duas reservas, sem resultado incerto. A consulta
  autorizada usou somente `mode=ro` e não alterou os bytes do banco.

A data da 101 é cerca de 68 segundos posterior à persistência. Não é possível
determinar retrospectivamente a causa dessa diferença, sincronização de relógios,
ordem de encaminhamento, mecanismo exato do update ou ocorrência de catch-up
apenas por horários ou pelo nome `NewMessage`. O log de recebimento ocorre após
persistir e não é uma medição do instante bruto de chegada do evento.

### Reprodução e política aplicada

RED pelos handlers/entrypoints reais, bancos temporários e transportes falsos:
uma mensagem criada dez minutos antes, cujo evento chega após prontidão, consumia
uma vaga, gerava e enviava. Singleton: uma TOP e um envio falsos; multi: duas TOP e
um envio falso. A lacuna confirmada era a ausência da comparação de criação no
handler, independentemente de como o evento antigo chegou. O startup limitado já
desabilitava catch-up deliberado e recovery de fila; isso não filtrava a data dos
eventos recebidos.

Agora o monitor registra o segundo UTC da prontidão imediatamente antes de
`mark_ready`. `Telegram listener connected` é emitido **depois** da abertura da
admissão. Antes de consumir vaga ou persistir, exige datetime com timezone e
normaliza `message.date` para segundos UTC. Só admite segundo estritamente maior;
todo o mesmo segundo é excluído por ambiguidade, inclusive quando uma fixture
fornece microssegundos. Não inventa precisão intrassegundo nem fuso para data
ingênua. Data ausente/inválida falha fechada, sem conteúdo ou valor em logs.

`excluded_events`/`exclusion_codes` são observações separadas, não eventos
aceitos/terminais. Não alteram `messages_received`, `processed`, `rejected`,
`skipped`, TOP, envio, reconciliação ou as vagas do piloto. Os códigos e logs
sanitizados estão descritos no roteiro. A mesma guarda cobre singleton, multi e
canônico dos comandos limitados; catch-up deliberado do monitor normal e leitura
manual continuam funcionando. Não há migration, avanço de checkpoint, mudança
de tracking/cache/reserva/contrato ou reparo de registros anteriores.

Limitação: timestamps de origem e relógio local precisam ser coerentes. A guarda
não corrige clocks nem identifica a causa de updates atrasados. O limite de tempo
e fechamento continuam usando o controlador monotônico existente. Aguarde um
segundo após a prontidão antes de enviar mensagens manuais.

O banco do piloto parcial permanece não vazio e preservado: uma futura execução
precisa de autorização/preflight para esse estado e mensagens novas. Reservas anteriores
continuam bloqueando reenvio; A/B já usados podem ter cache válido ou expirado,
sem que o histórico autorize reutilização. Não reaplicar automaticamente os
contadores esperados de um banco novo. Os cinco cenários foram concluídos depois
em outro banco, conforme a seção abaixo; a pendência documental anterior continua
separada.

### Verificação desta correção

Os resultados abaixo correspondem ao código corrigido, não ao HEAD anterior:

- RED: duas regressões falharam pelo motivo esperado, pois o evento antigo
  consumia a vaga e alcançava os transportes falsos singleton/multi.
- GREEN: 30 testes de admissão/entrypoint passaram; a rodada focada ampliada,
  incluindo monitor, CLI, piloto, limites e entrega, passou com 123 testes.
- Suíte final: **1085 aprovados, 5 live/browser excluídos, 6 warnings**, em
  469,62 segundos. As regressões de admissão somam 23 casos permanentes.
- `uv lock --check --offline`: aprovado, 41 pacotes.
- `uv run --offline --no-env-file ruff format --check .`: aprovado,
  232 arquivos formatados; Ruff check aprovado.
- `uv run --offline --no-env-file mypy src`: aprovado, 100 arquivos fonte.
- `git diff --check`: aprovado.

As fixtures de eventos existentes passaram a fornecer datas posteriores à
prontidão com relógio controlado; não houve relaxamento das regras de transporte,
tracking, cache ou entrega. Os seis warnings continuam sendo quatro ocorrências
do ciclo de FKs preexistente na autogeração Alembic e duas da depreciação do
adapter datetime sqlite3/Python 3.12. Não foram resolvidos nesta correção.

As verificações removeram credenciais do ambiente filho, desativaram gates
externos, impediram leitura do `.env` real e usaram sockets bloqueados, settings
sintéticas, transportes falsos e bancos temporários. A suíte exercitou migrations
somente nesses bancos temporários; não há migration nova. Não houve execução de
Telegram/TOP, piloto, push, merge ou alteração do Draft nesta tarefa.

## Piloto completo no SHA 5fd54c4

Em 16/10/2026, uma única execução no SHA
`5fd54c46f611e8b1e827cdf31b19caaac80e7276` concluiu os cinco cenários no destino
privado `private-test`: A gerou e foi entregue; A+B usou cache parcial e gerou B;
a repetição A+B usou cache total com nova entrega; A+B+A preservou três ocorrências
usando duas gerações; A+canônico foi rejeitado antes de TOP/envio adicional, sem
fallback. Mensagens de origem 102–106; entregas privadas 37–40. São IDs de mensagem,
não contadores globais de outros pilotos.

O [relatório detalhado do piloto](ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md#resultado-do-piloto-completo--16102026)
registra marcos UTC, limites, correlação e os dois bancos externos preservados.
Resultado desta tentativa: duas vagas TOP consumidas e duas gerações CONFIRMED;
quatro reservas, quatro PREVIEW_READY e quatro SEND_CONFIRMED, uma tentativa por
reserva; oito usos e 14 associações. Cache: dois casos completos e um parcial,
mais uma reutilização interna de A. Nenhum resultado incerto, envio parcial ou
duplicado observado; nenhuma publicação produtiva.

O JSON final encerrou por `max_messages`, com cinco mensagens processadas, código
zero, `failed=0` e uma rejeição esperada `ALIEXPRESS_COIN_SHORT_INVALID` (mensagem
106 em `FAILED_PERMANENT`). O processo encerrou, os gates foram desligados no
`finally` e os temporários foram removidos. Os registros duráveis e previews foram
conferidos em memória, sem imprimir URLs, tracking ou fingerprints.

**Piloto limitado aprovado; operação contínua não validada.** Houve
`excluded_events=0`: as recusas da guarda temporal continuam comprovadas pelos
testes offline, não por uma exclusão observada live. Tracking exato confirmado
nas duas gerações não comprova comissão, precedência, preço/cupom ou preservação
APP/PC/Moedas. O piloto parcial no SHA `2acc9b0` não é combinado a estes números;
a discrepância histórica de aproximadamente 68 segundos da mensagem 101 mantém
causa desconhecida. A pendência documental anterior continua separada.

Esta consolidação altera somente documentação. Reaproveita os 1085 testes offline,
cinco live/browser excluídos, 123 focados, seis warnings preexistentes e gates do
código `5fd54c4`, pois código, testes, lockfile e migrations permanecem idênticos.
Não afirma que a suíte foi reexecutada neste commit documental. A revisão do PR
e a decisão de retirar o Draft são posteriores; não há autorização para merge,
modo contínuo ou outra execução. O retorno às evidências não exige nova leitura
ou migration de bancos nesta tarefa.

Verificações da consolidação documental: seis testes focados de contrato/wrapper
aprovados, com configurações sintéticas, sem `.env` real ou rede; parsing do bloco
PowerShell sem erros e comparação com o wrapper/comandos anteriores sem alteração.
O diff foi conferido como exclusivamente Markdown, sanitizado e sem erros de
whitespace. A suíte completa não foi repetida; nenhum banco foi consultado,
migrado ou modificado nesta consolidação.

## Verificação anterior à correção P2

Resultados do HEAD `a1fd85555859b2c1fcd03f346121486f7140f480`; não validam
a correção P2 acima. A verificação do código corrigido é registrada separadamente.

| Verificação | Resultado |
|---|---|
| `uv lock --check --offline` | Aprovado, 41 pacotes |
| `uv run --offline ruff format --check .` | Aprovado |
| `uv run --offline ruff check .` | Aprovado |
| `uv run --offline mypy src` | Aprovado, 100 arquivos fonte |
| `uv run --offline pytest -m "not live and not browser"` | 1007 aprovados, 5 excluídos, 6 warnings; 319,08 segundos |
| `git diff --check` | Aprovado |
| Migration em SQLite temporário | Upgrade → downgrade → upgrade → check; preservação de singleton/histórico/reserva; recusa não vazia antes de DDL |
| SQL direto e purge | Constraints de spans/ordinal/READY/ponteiros; reservas e histórico preservados; `PRAGMA foreign_key_check` vazio |
| Wrapper/documentação | 5 preflights sintéticos; parsing PowerShell sem erros de sintaxe |

Os 76 testes focados originais passaram; depois da revisão, 17 testes de
entrypoint/migration/wrapper passaram, incluindo as duas novas regressões de
schema. A suíte final contém 78 testes adicionais sobre o baseline de 929; o
resultado do baseline não foi usado como validação do código novo.

Warnings: quatro ocorrências do aviso preexistente do SQLAlchemy sobre o ciclo de
FKs `affiliate_candidates`/`affiliate_link_proofs`/`deals` na autogeração Alembic
(a migration nova exercita mais um check), e duas ocorrências da depreciação do
adapter datetime do sqlite3/Python 3.12. A autogeração não ordena as FKs daquele
ciclo; não é uma validação completa delas. As FKs novas têm testes SQL e de purge
com enforcement ativo, além do check de integridade. Nenhum warning foi resolvido
como parte desta tarefa; não houve warning novo de recuperação/envio.

As alterações preexistentes em `.env.example` e `config.example.yaml` do checkout
original e o `.gitignore` foram conferidos por SHA-256 e permaneceram intactos.
Nenhum dos três arquivos está no diff da feature. `main` e sua referência remota
local permaneceram na base acima; não houve fetch, push ou merge nesta execução.

Os testes usam settings sintéticas, adapters falsos, `.env` real desabilitado e
sockets externos bloqueados. O wrapper documental é validado somente em preflight
com arquivos sintéticos e por parsing de PowerShell, nunca executado live.

## Decisões e limites

1. Ledger temporário em `.private` ignorada na worktree isolada, em substituição
   aos helpers bash da skill no Windows. Custo: bookkeeping manual; não foi
   alterado `.gitignore` e nenhum arquivo do operador foi incluído.
2. Fixture Telegram preexistente corrigida de offset 31 para 30 unidades UTF-16,
   que é o tamanho real do prefixo sintético. Custo: o cenário válido deixa de usar
   um span incoerente; quatro casos de spans inválidos continuam recusados.
3. Exigir schema atual antes de operações reais, em vez de aceitar um banco que
   falharia depois de construir transportes. Custo: upgrade explícito necessário;
   bancos antigos e consultas read-only não são alterados automaticamente.
4. Comissão e experiência APP/PC/Moedas não são comprovadas pelos testes offline
   ou pelo tracking confirmado. Custo: evidência manual/financeira continua exigida.
5. Lock/deduplicação não abrangem bancos diferentes, versões antigas ou outras
   máquinas. Custo: o operador precisa impedir listeners concorrentes fora do lock.
6. O piloto parcial anterior foi interrompido; o piloto completo posterior
   concluiu os cinco cenários no SHA `5fd54c4`, apenas no escopo limitado privado.
   Custo: não valida operação contínua; novas execuções exigem autorização e
   consideração de cache/TTL, reservas e bancos de evidência não vazios.

Não houve itens menores adiados na revisão. Preços, moedas, descontos e cupons
copiados permanecem informações da origem; a pendência documental anterior do
piloto não foi considerada concluída por esta implementação.

O [roteiro PowerShell completo](ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md) prepara
somente um banco novo permanente externo, inclui pré-checagem sanitizada de
tracking, lock/processos, limites e desligamento de gates em `finally`.
