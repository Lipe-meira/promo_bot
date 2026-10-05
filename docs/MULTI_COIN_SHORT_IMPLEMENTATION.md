# Implementação: múltiplos shorts no listener shadow

Base local examinada: `8f0da4a22b1f9e21dd5b93bb0599c55077d25f0b`.
Branch isolada: `codex/multi-coin-short-shadow`. Nenhum push, merge ou teste live.

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

O validador compartilhado agora confere tabelas e revisão com uma conexão SQLite
`mode=ro`, `query_only=ON`, timeout de lock zero e uma transação de leitura, antes
de abrir sua primeira sessão gravável. **Não usa `immutable`**, pois isso poderia
ignorar commits no WAL. A comparação física ocorre antes de qualquer limpeza ou
outra conexão de verificação. A regressão GREEN preserva byte a byte o arquivo
principal e o WAL; uma migration explícita confirmada somente no WAL também é
reconhecida corretamente.

O `-shm` é estado de coordenação, não histórico: SQLite pode atualizar read marks
ou reconstruir seu índice existente durante a leitura. Não há garantia de
preservação byte a byte desse sidecar; isso é coberto por teste com índice stale.
Banco cujo header indique modo WAL exige os dois sidecars já existentes. Mesmo
depois de um fechamento limpo, sem transações pendentes, `mode=ro` pode criar um
WAL vazio; a regressão adicional reproduziu esse caso. Por isso, modo WAL sem
`-wal`/`-shm`, ou WAL não vazio sem `-shm`, é recusado conservadoramente com
`AFFILIATE_HISTORY_WAL_UNVERIFIABLE`, sem abrir SQLite, criar sidecar, apagar WAL
ou tentar abertura gravável. O header só escolhe a guarda de sidecars; a revisão
continua sendo consultada pela visão transacional SQLite, nunca pelo header.
Isso pode impedir a admissão de um banco WAL válido fechado sem sidecars. Não há
manutenção nem recriação automática nesta entrega. Falha de leitura/lock/recovery
retorna
`AFFILIATE_HISTORY_STORAGE_UNAVAILABLE`, sem fallback. Não se tenta recuperação
automática; manutenção externa, se necessária, exige decisão separada do operador.

Esta checagem não é backup nem impede alterações por processos externos. O lock
do listener continua obrigatório; não migrar/substituir o banco concorrentemente.
Para um banco admitido, permanece a validação gravável completa de arquivo,
revisão, durabilidade, `synchronous=FULL`, integridade e FKs antes dos transportes.
O fechamento de um banco **admitido** pode executar checkpoint normal. Tracking,
gates, contratos, claims, cache, orçamento e reservas não foram alterados.

## Verificação da correção P2

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

O P2 de preservação física está corrigido. Não foi identificado outro bloqueador
para publicar a branch/abrir PR nesta correção. A recusa conservadora de WAL sem
sidecars e a ausência de exclusão de interferência externa permanecem limitações
explícitas, não uma autorização para manutenção automática. Nenhum push/merge,
acesso a banco real ou chamada live foi feito; a pendência documental anterior
permanece separada.

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
6. Nenhuma validação live de TOP/Telegram foi realizada. Custo: o teste privado
   limitado permanece uma etapa futura, com autorização separada.

Não houve itens menores adiados na revisão. Preços, moedas, descontos e cupons
copiados permanecem informações da origem; a pendência documental anterior do
piloto não foi considerada concluída por esta implementação.

O [roteiro PowerShell completo](ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md) prepara
somente um banco novo permanente externo, inclui pré-checagem sanitizada de
tracking, lock/processos, limites e desligamento de gates em `finally`.
