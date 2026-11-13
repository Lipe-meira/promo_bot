# Checkpoint backend — 12/11/2026

## S1 — WorkspaceRuntimeSpec (13/11/2026)

Objetivo: configuração explícita e isolada por workspace para o futuro Web SaaS
([direção](../SAAS_ARCHITECTURE.md)). Escopo: só a S1 e o registro arquitetural inicial. Sem API,
frontend, Clerk, cofre, worker, login QR, PostgreSQL, providers, publicação ou migrations. S2–S13
**não** foram iniciadas.

- Branch: `codex/workspace-runtime-spec`, criada de `origin/main` em `cde93c3` (merge do PR #8), em
  worktree separado `F:\projetos\promo_bot_s1`. O checkout `F:\projetos\promo_bot` (branch antiga
  `codex/runtime-migration-safety`, com `.env.example` e `config.example.yaml` modificados) ficou intacto.
- Implementação: `src/promo_bot/config/workspace.py` (commit `d91871900a8b451beb2add19f571ba9d2385cca7`, validado) e
  `tests/unit/test_workspace_runtime_spec.py`. Documentação: `2786377faed4f387ec4f7be27f05b1480a7615e7`.
- `WorkspaceRuntimeSpec.build(workspace_id, runtime_root=, settings=, app_config=)` devolve o mesmo
  `EnvironmentSettings` e `AppConfig` do runtime. Decisões: subclasse de `EnvironmentSettings` cuja única
  fonte é o construtor (sem env/.env); allow-list de campos (gates e caminhos recusados); UUID canônico
  único; caminhos derivados de `<raiz>/<uuid>/`; recusa de raiz relativa/`..`/raiz de disco/nome ambíguo
  do Windows/link que escape/sobreposição com `~/.promo_bot`; providers e navegador não ativáveis;
  gates conferidos após a construção; erros com código estável, sem eco de valores.
- CLI legada e `EnvironmentSettings` não foram alterados; `cli.py` não importa o módulo novo.
- Validações (worktree, offline): `ruff format --check`, `ruff check`, `mypy src` e `uv lock --check
  --offline` limpos; `pytest -m "not live and not browser"`: 1675 passed, 1 skipped, 5 deselected. O skip
  é o teste de symlink escapando da raiz (symlinks indisponíveis sem privilégio no Windows): **esse
  caso não foi exercitado**. Live/browser não executados. Mutação manual (fonte de env reativada) é
  detectada pelo teste de ambiente contaminado.
- Limitações: `safe_summary()` inclui o caminho local do runtime; sem lock/criação de diretório (será do
  supervisor, S8); allow-list cobre só Telegram/AliExpress/limites/log (Shopee, Amazon, Awin, ML não);
  destinos shadow em `AppConfig` continuam permitidos (gate de entrega fica no ambiente, fechado);
  `_reject_ambiguous_windows_name` foi duplicado para não importar o módulo de banco (candidato a
  utilitário compartilhado).
- Próximo passo: auditoria independente do Codex sobre a S1; **aguardar aprovação** antes de qualquer
  outra tarefa. Merge e deploy não autorizados.
- **Auditoria independente da S1 (SHA `cb221bd`, NO-GO)** — correções no commit `7b949103ed76651e567062b8c2fdd02a5bd509d0` (código e testes; **commit validado**):
  - H-01: `AppConfig` era compartilhado entre workspaces (dicts mutáveis sob `frozen=True`). Agora é
    reconstruído de dados simples e os dicts são `_FrozenDict` (`dict` que recusa mutação).
  - M-01: só `X:\...` no Windows; recusa de `\?\`, UNC, nomes reservados, caracteres inválidos, raiz que
    seja arquivo; sobreposição com o runtime legado/`PROMO_BOT_RUNTIME_DIR` por caminho resolvido; URL
    SQLite com round-trip no SQLAlchemy (sem conexão).
  - L-01: `log_level` mapeado ao alias interno; valor gerenciado não aplicado é recusado.
  - L-02: NUL e falhas de resolução/`stat` viram `WorkspaceSpecError` sem eco.
  - Gate guard: tabela `_CLOSED_GATES` + testes (valor perigoso, default futuro simulado, e
    classificação obrigatória de novo booleano).
  - Testes: `tests/unit/test_workspace_runtime_spec_audit.py` (RED: 96 falhas; GREEN depois). O teste
    antigo de symlink (sempre pulado no Windows) foi substituído por um de junção/symlink que executa.
  - Validação: ruff, mypy, lock e `diff --check` limpos; suíte offline 1817 passed, 0 skipped. Mutação
    manual de 11 pontos das correções é detectada pelos testes.
  - Limites restantes: UNC/long paths/TOCTOU não validados (ver `SAAS_ARCHITECTURE.md`).

## T5A — F-07: migrations automáticas do banco runtime (12/11/2026)

Objetivo: impedir que iniciar o bot altere um banco runtime existente sem proteção. Escopo: só F-07
(F-06 fica para a T5B). Sem migration nova, sem providers, publicação, busca ou envios, e sem
migrations em bancos reais. A T4 foi integrada à `main` pelo merge
`abc103734176b6fe5bed7dba519aa7fe2d473ff4`.

- Repositório: `Lipe-meira/promo_bot`.
- Branch: `codex/runtime-migration-safety` (reutilizar ao continuar), criada de `origin/main` em
  `abc103734176b6fe5bed7dba519aa7fe2d473ff4`.
- Commit de implementação inicial: `10c6261878dc149f151f1b12b8fd86fb50c9e0bb`; SHA publicado após a
  primeira auditoria: `76a098648c27fb3ab6630c187fde3c6761492bb6`.
- Complemento de segurança (primeira auditoria): commits `ef208ee` (código e testes),
  `1165fad` (teste de isolamento), `747a95d` (documentação) e `ea28376` (checkpoint; SHA completo
  `ea283762b66d3fb943f329115a99af8972338847`).
- Segunda auditoria independente (H-02, H-03, M-01, M-02, M-03, L-01): `77b19d7` (código, testes e
  `.gitignore`), `de43742` (dois testes extras; **commit de código final validado**, SHA completo
  `de4374244283f65c23c986fa7203446bca35061a`) e commits de documentação posteriores. Este checkpoint é um
  commit posterior; consulte o SHA final pelo Git (`git log`, `git ls-remote`).
- Auditoria final (SHA `b54d451f5d910f2e5da7f370a14038260d18d1d5`, veredito NO-GO por **N-01**): correção
  no commit `428c867` (código e testes; **commit de código validado**) e documentação/checkpoint em commit
  posterior. Ver "Terceira auditoria: N-01".
- Diff: [main...codex/runtime-migration-safety](https://github.com/Lipe-meira/promo_bot/compare/main...codex/runtime-migration-safety).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/runtime-migration-safety`.
- Merge na `main`: **não autorizado**. T5B e T6 não foram iniciadas.

### Causa-raiz e evidências (bancos temporários)

- `command_listen` chamava `upgrade_database()` em toda inicialização; `init-db` fazia o mesmo.
  Banco ausente: arquivo e diretório pai criados. Banco no head: Alembic aberto com escrita, sem
  mudanças. Revisão antiga: migrations aplicadas direto, sem aviso e sem backup. Revisão desconhecida:
  `CommandError` do Alembic. Arquivo não SQLite: `DatabaseError`.
- Falha no meio da cadeia (revisão simulada que lança erro): o arquivo ficou com tabelas novas e uma
  `_alembic_tmp_*` gravadas e `alembic_version` antigo; a nova tentativa falhou com "table already
  exists". O DDL não é atômico com o driver atual e o banco fica inutilizável sem backup.
- O runtime usa journal `delete` por padrão; WAL só existe se o arquivo já estiver nesse modo.
  Copiar o arquivo principal de um banco WAL perde commits do `-wal` (provado em teste).

### Decisão

Bloquear upgrade implícito e exigir `init-db` explícito com backup verificado (detalhes e garantias em
[Migrations do banco runtime](../ARCHITECTURE.md#migrations-do-banco-runtime)). Descartadas: backup
automático dentro do `listen` (continuaria alterando o banco sem decisão do operador) e migração atômica
via `BEGIN` explícito no driver (afeta todas as migrations e os bancos shadow).

Mudança de operação do CLI, intencional e dentro do objetivo da tarefa: `listen` com banco existente
desatualizado agora sai com código 2 e pede `init-db`; `init-db` ganhou os campos `backup_created` e
`backup_name` e o status `current`; backup `*.bak` aparece ao lado do banco após um upgrade.

### Complemento de segurança da auditoria (F1/F3, F2, F4 e isolamento dos testes)

Achados da auditoria, todos reproduzidos offline: `init-db` migrava com um listener já ativo (o lock
O_EXCL só existia durante o `init-db`); um `init-db` morto à força deixava `.migrate.lock` órfão
(12 de 12 mortes no meio da migration); dois pontos de erro vazavam ou quebravam o contrato (um
`sqlite3.Error` sem tratamento saía com traceback e um `OSError` em `_make_backup` imprimia o caminho);
e a recuperação documentada mandava apagar os sidecars, o que destrói commits do `-wal` posteriores ao
backup.

- **F1/F3, causa-raiz:** a proteção era um arquivo `O_EXCL` solto durante a migration, e o listener não
  segurava nada. **Correção:** dois locks cooperativos do SO (`msvcrt.locking`/`flock`), sem bloqueio e
  sempre na ordem migração, depois listener. `listen` mantém o de listener durante toda a vida do
  monitor (`runtime_listener_session` em `cli.py`); `init-db` mantém os dois durante todo o upgrade.
  Códigos: `RUNTIME_DB_LISTENER_ACTIVE`, `RUNTIME_DB_MIGRATION_IN_PROGRESS`, `RUNTIME_DB_LOCK_FAILED`.
  O lock é do SO: liberado na morte do processo (atraso máximo medido: 6 ms), sem órfão. O
  `ShadowListenerLock` serviu de padrão, mas não foi alterado nem reutilizado (código de erro, arquivo e
  testes próprios do shadow).
- **F4, causa-raiz:** leituras e operações de arquivo fora de qualquer `try`. **Correção:** a leitura da
  revisão vira `RUNTIME_DB_UNREADABLE`; tamanho e promoção do backup viram `RUNTIME_DB_BACKUP_FAILED`;
  um guarda final converte qualquer `OSError`/`sqlite3.Error` inesperado em `RUNTIME_DB_STORAGE_ERROR`.
- **F2:** o procedimento de recuperação em `docs/ARCHITECTURE.md` agora exige parar todos os processos,
  verificar o backup, mover (não apagar) banco e sidecars juntos para uma quarentena, copiar o backup
  verificado e preservar tudo para investigação. Declara que gravações posteriores ao backup não voltam
  automaticamente. Nenhuma restauração automática foi implementada.
- **Isolamento global dos testes:** `tests/conftest.py` redireciona `HOME`, `USERPROFILE` e
  `PROMO_BOT_RUNTIME_DIR` (igual ao layout padrão dentro da sandbox) e remove `PROMO_BOT_DATABASE_URL`
  em todo teste offline; testes que precisam de outros valores os definem com `monkeypatch.setenv`.
  `tests/unit/test_offline_isolation.py` prova a sandbox, a configuração explícita e, em um pytest filho,
  que variáveis `PROMO_BOT_*` herdadas do shell do operador são sobrescritas.
- **Registrados e não corrigidos:** F5 (journal transitório tratado como `UNREADABLE`) e F6 (`CURRENT`
  significa só `alembic_version == head`), ambos descritos em `docs/ARCHITECTURE.md`.

### Validações do complemento

Cópia limpa (worktree destacado) do commit `747a95d`; cada código de saída foi capturado do próprio
processo, e o runtime foi isolado pelo `conftest` e também por um `HOME` externo de sandbox:

- `uv lock --check --offline`: exit 0 (41 pacotes). `ruff format --check .`: exit 0 (251 arquivos).
  `ruff check .`: exit 0. `mypy src`: exit 0 (104 arquivos). `git diff --check`: exit 0.
- `uv run --offline pytest -m "not live and not browser"`: exit 0, **1.510 aprovados, 5 excluídos,
  6 avisos em 645 s**. A sandbox de `HOME` terminou vazia e o arquivo real em `~/.promo_bot` manteve
  tamanho e data de modificação inalterados antes e depois.
- Testes novos e comprovações com **processos independentes** (`subprocess`, não threads): listener
  ativo bloqueia `init-db` (banco novo e banco pendente), `init-db` em andamento bloqueia o listener,
  segundo listener recusado, três listeners simultâneos em banco novo (exatamente um sobe), morte
  forçada do listener e do `init-db` no meio do backup (lock liberado pelo SO, `.partial` obsoleto nunca
  promovido), `listen` segurando o lock durante todo o monitor, erros de lock e de SQLite sanitizados.
  Executados 6 vezes seguidas sem falha depois de corrigir uma corrida do próprio teste (o Windows libera o
  lock cerca de 6 ms depois do fim do processo; o teste espera a liberação).
- Mutação: 21 mutantes nas proteções novas (sem lock de listener ou de migração no `init-db`, lock solto
  antes do monitor, lock de migração mantido, `listen` sem sessão, lock não reconhecido como ocupado,
  chamada do SO ignorada, guarda de erros desligado, três pontos de erro sem tratamento, erro de lock sem
  sanitizar, pendente sem bloqueio, sem backup, sem `integrity_check`, cópia ingênua e três variáveis do
  conftest). Todos mortos, exceto um **equivalente no CPython** (não fechar o arquivo em `release`: o
  desbloqueio explícito já ocorreu e a contagem de referências fecha o arquivo). Dois mutantes do conftest
  sobreviveram na primeira rodada e ganharam o teste do pytest filho.
- Não executados: testes live/browser, qualquer comando ou migration em banco real, Telegram, deploy.

### Validações da primeira rodada

Executadas em cópia limpa (worktree destacado) do commit de implementação `10c6261`:

- `uv lock --check --offline`: aprovado (41 pacotes).
- `uv run --offline ruff format --check .`: aprovado (250 arquivos). `ruff check .`: aprovado.
- `uv run --offline mypy src`: aprovado (104 arquivos).
- `uv run --offline pytest -m "not live and not browser"`: **1.492 aprovados, 5 excluídos, 6 avisos em
  647 s**, sem falhas (inclui os 36 testes novos).
- `git diff --check` nos commits da tarefa: aprovado (avisos de CRLF do Windows são só do Git).
- Testes novos em `tests/unit/test_runtime_migration_safety.py` (36): banco novo, atual, antigo,
  desconhecido, sem versão, ilegível e com journal; `listen` bloqueado sem iniciar o monitor; backup com
  WAL (commit só no `-wal` presente no backup; cópia ingênua o perderia), interrompido, truncado,
  corrompido numa página, com revisão errada e com sidecar; sem espaço; erros de permissão/disco
  sanitizados; falha de migration com backup mantido; lock; concorrência; corrida com outro upgrade.
  Antes da implementação a coleta falhava (módulo inexistente). Dezoito mutações (startup migrando, sem
  bloqueio de pendente, sem backup, sem verificação, sem `integrity_check`, sem checagem de espaço, cópia
  ingênua, `.partial` não limpo em interrupção, lock ignorado, sem nova inspeção, corrida não
  descartada, sem versão aceito, journal ignorado, revisão desconhecida como pendente, banco novo
  falho não limpo, vazamento de caminho, sidecar aceito, banco atual abrindo Alembic): 3 sobreviveram
  na primeira rodada e ganharam testes; na rodada final nenhuma sobrevive.
- Gates: o diff de código toca só `cli.py` (a chamada de `listen` e o `init-db`), `migrations.py`
  (helper de leitura) e o módulo novo; nenhum gate, listener, caminho shadow ou envio foi alterado.
- Não executados: testes live/browser, migrations ou qualquer comando em bancos reais, Telegram, deploy.

### Segunda auditoria independente: matriz dos oito achados

A matriz original do Codex **não está no repositório nem no remoto** e a mensagem que a citava descreve só
os achados H-02, H-03, M-01, M-02, M-03 e L-01. H-01 e H-04 foram declarados como já corrigidos e validados,
mas a sua descrição não esteve disponível nesta sessão; **não os mapeei por suposição**. O que se pode
afirmar é que nenhuma proteção da etapa anterior foi removida ou enfraquecida: os testes dela (locks entre
processos, kill forçado, concorrência, erros sanitizados do F4 e isolamento) seguem no arquivo e verdes.
Confirmar com o autor da matriz qual achado cada um desses testes cobre.

| ID | Estado | Causa-raiz | Evidência de resolução | Risco residual |
| --- | --- | --- | --- | --- |
| H-01 | Preservado (descrição indisponível) | n/d | Suíte completa verde; testes da etapa anterior intactos | Mapeamento a confirmar |
| H-02 | **Corrigido** | Inspeção, locks e backup usavam `expanduser()+resolve()`; a engine do Alembic e o listener abriam a URL crua (`~` literal) | RED: o banco protegido continuou na revisão antiga e o listener conectou a `./~/rt.sqlite3`. GREEN: `test_every_url_spelling_protects_backs_up_and_migrates_the_same_file` (3 grafias, dois bancos sintéticos, o não protegido fica byte a byte igual e sem backup), `test_listener_connects_to_the_very_database_it_protected`, `test_identity_helpers_agree_for_the_same_file`, `test_canonical_url_refuses_a_path_that_would_not_round_trip`. 4 mutantes mortos | Aliases de volume que não são hard link, symlink nem junction não foram exercitados |
| H-03 | **Corrigido** | Locks nomeados pelo caminho: outro nome (hard link) do mesmo inode tinha outros locks; o SQLite mantém journals por nome | RED: `DID NOT RAISE` (a migration rodava pelo alias com o listener ativo). GREEN: `test_hard_link_alias_cannot_bypass_an_active_listener_lock[delete\|wal]` (processo independente), `test_hard_linked_database_is_refused_by_listen_and_init_db_without_any_write`, `test_alias_created_while_upgrading_is_caught_before_the_migration`. 5 mutantes mortos | Janela entre a última checagem de `st_nlink` e a escrita; o lock não é por inode |
| H-04 | Preservado (descrição indisponível) | n/d | Idem H-01 | Mapeamento a confirmar |
| M-01 | **Corrigido** | Sem o arquivo principal o banco era `MISSING`; criar um novo fazia o SQLite descartar sidecars | Evidência real: um `-wal` órfão com commit foi **apagado**. GREEN: `test_orphan_sidecar_without_main_database_is_preserved_and_blocks_everything[-wal\|-journal\|-shm]` (bytes idênticos, nenhum banco criado, CLI recusa). 2 mutantes mortos | Recuperar o conteúdo do órfão é manual (quarentena) |
| M-02 | **Corrigido** | `handle.close()` do lock fora de qualquer tratamento e sem proteger o erro em andamento | GREEN: `test_lock_release_failure_is_sanitized_and_still_releases_the_lock`, `test_start_up_release_failure_of_the_migration_lock_does_not_leave_the_listener_lock_held`, `test_lock_release_failure_exits_the_cli_with_a_controlled_error`, `test_release_failure_never_masks_an_error_already_in_flight`. 4 mutantes mortos | Quando é o único erro, o trabalho já terminou |
| M-03 | **Documentado e caracterizado** (sem mudança de código) | `CURRENT` é só `alembic_version == head`; não há `integrity_check` na inicialização | `test_current_status_is_not_a_physical_integrity_check_documented_limit` e `test_init_db_refuses_to_migrate_a_physically_corrupt_pending_database` (ambos passavam antes: são caracterização). Texto técnico em `docs/ARCHITECTURE.md` | Banco CURRENT fisicamente corrompido inicia; `quick_check` opcional fica para tarefa própria |
| L-01 | **Corrigido** | `.gitignore` só cobria `*.sqlite3` | `test_gitignore_covers_runtime_backups_partials_locks_and_sidecars` (7 nomes via `git check-ignore`); `uv.lock` continua rastreado. 5 mutantes mortos | Nenhum |

Resultados da segunda auditoria, em cópia limpa (worktree destacado) do commit `de43742`, runtime isolado pelo
`conftest` e por um `HOME` externo, exit code lido do próprio processo:

- `uv lock --check --offline`, `ruff format --check .` (251 arquivos), `ruff check .`, `mypy src` (104 arquivos)
  e `git diff --check`: todos exit 0.
- `uv run --offline pytest -m "not live and not browser"`: exit 0, **1.536 aprovados, 5 excluídos, 6 avisos em
  698 s**. Os dois arquivos desta tarefa somam 80 testes. A sandbox de `HOME` terminou vazia e o arquivo real em
  `~/.promo_bot` manteve tamanho e data de modificação.
- Antes da correção, 19 dos testes novos falharam (RED); 78 passaram em 3 execuções seguidas depois.
- Mutação: 20 mutantes (identidade, conexão do listener, ida e volta da URL, engine, guarda de hard link e
  suas três checagens, sidecars órfãos, liberação dos locks e cinco padrões do `.gitignore`): **20 mortos,
  0 sobreviventes**, arquivos restaurados e conferidos.
- Não executados: testes live/browser, qualquer comando ou migration em banco real, Telegram, deploy.

### Terceira auditoria: N-01 (HIGH) — identidade instável de nomes Windows

O Windows descarta pontos e espaços finais ao **criar** o arquivo. Um banco ainda inexistente chamado
`runtime.sqlite3.` era criado como `runtime.sqlite3`, mas `resolve()` mantinha a grafia antiga e os locks e
sidecars derivados dela pertenciam a outro nome. Efeitos (H-01, H-02, M-01, um só defeito): dois
`command_listen` em processos distintos sobre o mesmo arquivo, migration com DDL sob listener ativo e
descarte de WAL/journal órfão. Relatório: auditoria independente do Codex (fora do repositório).

- Correção (fail-closed, sem sistema genérico de identidade): `_database_path` chama
  `_reject_ambiguous_windows_name` sobre o texto após `~`, antes de `resolve()`. Recusa, com
  `RUNTIME_DB_URL_UNSUPPORTED`, todo componente (separadores `/` e `\`) terminado em `.` ou espaço, exceto
  `.` e `..`, existisse o arquivo ou não. Como todas as entradas públicas (`inspect_schema`,
  `upgrade_runtime_database`, `runtime_listener_session`, `init-db`, `listen`) passam por `_database_path`, a
  recusa ocorre antes de locks, sidecars, SQLite e criação de arquivos. Também recusa qualquer `?` na URL
  original (o parser descartava uma query sem `chave=valor`; lacuna apontada no relatório, sem bypass de lock
  demonstrado). Só no Windows (`os.name == "nt"`).
- RED (Windows, bancos sintéticos): 20 dos novos testes falharam pelo motivo certo: a grafia ambígua chegou a
  `READY missing`, o WAL/journal órfão foi aceito (`DID NOT RAISE`) e a grafia ambígua foi processada em vez
  de recusada (`RUNTIME_DB_LISTENER_ACTIVE` no lugar de `URL_UNSUPPORTED`). Os 8 testes de grafias normais passaram
  já no RED (guardas de regressão). O teste do `?` foi escrito depois da correção correspondente; a sua
  eficácia foi provada por mutação (M3), não por RED.
- GREEN: `tests/unit/test_runtime_migration_safety.py` com 108 testes aprovados. Cobrem ponto e espaço finais,
  componentes intermediários, `..`/`.` estruturais, `~` expandido para home terminado em ponto, recusa sem
  nenhum efeito (comparando a árvore de arquivos), banco existente, dois e três processos listener (verificando
  os arquivos e os locks realmente criados), listener no nome normal bloqueando migration pedida pela grafia
  ambígua com grafo sintético aditivo (`ffffa001`; DDL não aplicado), WAL real órfão com commit recuperável
  (bytes preservados, nenhum banco criado), journal órfão e grafias normais (espaço interno, pontos internos,
  nome iniciado por ponto, segmentos `.`/`..`). Hard links, `~` e demais testes anteriores seguem verdes.
- Mutação (8 mortos, 1 equivalente): sem a checagem; sem a recusa de `?`; só ponto; só espaço; sem separar
  `\`; sem isentar `.`/`..`; só o último componente; checagem desligada. Sobrevivente equivalente: trocar o
  código do erro interno, pois `_database_path` converte todo erro em `RUNTIME_DB_URL_UNSUPPORTED`. Uma
  checagem redundante sobre a URL antes do `~` foi removida porque o seu mutante sobrevivia.
- Validação: `uv lock --check --offline` exit 0; `ruff format --check .` 251 arquivos; `ruff check .` e
  `mypy src` (104 arquivos) limpos; `git diff --check` exit 0. Suíte offline completa em worktree limpo
  destacado em `b54d451` com só os dois arquivos da correção: **1.569 aprovados, 5 excluídos, 6 avisos, exit
  0, 613 s**. Na primeira execução, no diretório de trabalho, a suíte deu **10 falhas e exit 1**, todas em
  testes que leem `config.example.yaml`/`.env.example` locais do operador (preservados e fora dos commits, já
  descritos abaixo); os mesmos arquivos de teste passam no worktree limpo.
- Riscos residuais: janela TOCTOU externa de hard links (H-03) e M-03 (integridade física de bancos
  CURRENT) permanecem como documentados e aceitos, sem mudança. A regra não cobre fluxos alternativos `:`
  nem nomes reservados, nem aliases de volume. Não executei o N-01 contra o runtime real em `~/.promo_bot`.
- Não executados: live/browser, comandos ou migrations em bancos reais, Telegram, deploy, merge.

### Riscos residuais e pendências

- Bancos shadow (`cli.py` shadow preview, `discovery/runtime.py`, `discovery/sku_runtime.py`) ainda
  migram implicitamente; fora do escopo (banco runtime) e não alterados.
- Locks cooperativos: só `listen` e `init-db` os respeitam (o shell `sqlite3` e outras ferramentas não),
  valem numa só máquina e não foram exercitados sob falha de energia.
- Sem restauração automática após migration que falha; ver o procedimento de recuperação na arquitetura.
- Backups `*.bak` não são rotacionados nem apagados pelo bot.
- Acidente de teste (corrigido): uma versão inicial dos testes criou um `promo_bot.sqlite3` **vazio**
  (schema head, só `alembic_version`) em `~/.promo_bot`, porque `database_url=` é ignorado pelo
  `EnvironmentSettings` (só vale o alias `PROMO_BOT_DATABASE_URL`). Evidências: nascimento às 18:23:19,
  0,58 s de vida, irmãos de setembro/novembro e a falha de asserção da época. A inexistência prévia é
  inferência, não prova: não havia instantâneo anterior. O arquivo e os seus sidecars foram preservados
  e **nunca** foram alterados, renomeados nem apagados desde então (conferido por tamanho e data antes e
  depois de cada validação). A decisão sobre ele é do operador. O `conftest` global impede repetição.
- `config.example.yaml` local (preservado, fora dos commits) continua quebrando `test_validate_config`
  e afins nesse checkout; a suíte completa é validada em cópia limpa.


## T4 — F-03: configurações aceitas mas não aplicadas (12/11/2026)

Objetivo: eliminar configurações que parecem impor regra de segurança, publicação ou filtragem e não
têm efeito. Escopo: só F-03. Nada de T5, providers, publicação, busca, agendadores, migrations, operações
live ou envios pelo bot. A T3 foi integrada à `main` pelo merge
`69b313737b7540c248f9d5e5298b921a7ebdd6a7`.

- Repositório: `Lipe-meira/promo_bot`.
- Branch: `codex/config-policy-contract-hardening` (reutilizar ao continuar), criada de `origin/main` em
  `69b313737b7540c248f9d5e5298b921a7ebdd6a7`.
- Commit de implementação verificado: `4f991d845e7b4cfdcf0e8badf86395d2172f0727`. Este checkpoint é um
  commit posterior; consulte seu SHA pelo Git.
- Diff: [main...codex/config-policy-contract-hardening](https://github.com/Lipe-meira/promo_bot/compare/main...codex/config-policy-contract-hardening).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/config-policy-contract-hardening`.
- Merge na `main`: **não autorizado**. Aguardar autorização explícita.

### Matriz de classificação (fonte: `src/promo_bot/config/policy.py`)

| Campo | Situação | Evidência |
| --- | --- | --- |
| `source_channels`, `telegram_relay`, `telegram_shadow_delivery`, `providers` | Aplicado | Lidos em `cli.py`, `relay/`, `affiliate/shadow_delivery.py`, `coin_shadow_delivery.py`, `discovery/runtime.py` (gates) |
| `categories` | Parcial | Só `cli.py:416` (`run` registra a contagem); nada filtra |
| `keywords`, `minimum_discount_percent`, `minimum_score`, `cooldown_hours`, `maximum_promotions_per_hour`, `search_intervals_minutes`, `provider_rate_limits_per_minute`, `templates`, `affiliate_disclosure`, `presentation_timezone` | Reservado (aceito, não aplicado) | Nenhum consumidor de produção; mensagens usam `relay/formatter.py` fixo |
| `blacklist`, `allowed_sellers`, `blocked_sellers`, `maximum_price` | Recusado se preenchido | Eram inertes; agora `CONFIG_POLICY_NOT_ENFORCED` |

### Causa-raiz e decisões

- Nenhum campo de política é lido: não existe pipeline de catálogo (preço, vendedor, score, desconto por
  oferta). `DiscoveryProfile.keywords` é outro modelo, com arquivo próprio. `ShopeeEnrichmentService` e
  os formatadores `render_ready_*` não têm chamador operacional. As entregas shadow partem de mensagens-fonte
  e seus candidatos não carregam preço, vendedor nem desconto, então não há onde aplicar os filtros sem
  inventar dados. `MAX_PROMOTIONS_PER_HOUR` do ambiente também só é validado.
- Mudança incompatível justificada: um valor não vazio nesses 4 campos agora falha o carregamento.
  Antes era ignorado em silêncio, o que é pior (um vendedor listado parecia bloqueado). Os valores
  padrão (listas vazias e `null`) continuam válidos. Verificado: o `config.example.yaml` de `origin/main`
  carrega e o `config.yaml` local do usuário não preenche nenhum dos 4 campos (checado só por booleanos).
- Não adotado: aplicar os filtros em `shadow_delivery`/`coin_shadow_delivery` (sem dados do produto),
  rejeitar valores personalizados dos limites numéricos (quebraria configs válidas, sem ganho de
  segurança), renderizar `templates` arbitrários (exige análise de segurança própria) e avisos a cada
  carga (ruído, pois o exemplo já preenche vários reservados).
- Documentação: `README.md` ("Configuração: o que é aplicado") e nota em `docs/PRODUCT_SPEC.md`.
  `config.example.yaml` não foi alterado (tem alterações locais do usuário).
- Skills usadas: `superpowers:systematic-debugging`, `superpowers:test-driven-development` e
  `superpowers:verification-before-completion`.

### Validações

Em cópia limpa do commit `4f991d8` (`git archive`):

- `uv lock --check --offline`, `ruff format --check .` (248 arquivos), `ruff check .` e `mypy src`
  (103 arquivos): aprovados.
- `pytest -m "not live and not browser"`: **1.456 aprovados, 5 excluídos, 6 avisos em 642,51 s**
  (referência da T3: 1.416; a diferença são os 40 testes novos). Sem falhas.
- RED: `tests/unit/test_config_policy_contract.py` com a matriz e sem o validador: 6 falhas (os campos
  recusados eram aceitos) e 34 passes de caracterização. GREEN: 40/40 e `test_config.py`.
- O guarda de deriva confere a matriz com o código: campos reservados não podem ser lidos por
  `config.<campo>` em `src/`, e os aplicados/parciais precisam ser. Limite: só detecta acesso por
  variável terminada em `config`; conferi à mão acessos por outros nomes e dinâmicos (`getattr`,
  `model_dump`): nenhum em campos de política.
- Mutações (cada uma derruba testes): validador que nunca rejeita (6), `maximum_price` como reservado
  (3), `keywords` como aplicado (1), `categories` como reservado (2), campo fora da matriz (1),
  mensagem que ecoa o valor (1).
- Local (checkout com as alterações do usuário): `test_example_yaml_is_valid` falha por causa do
  placeholder de destino do `config.example.yaml` local, já descrito nas pendências. Não é regressão.
- Não executados: testes live/browser, operações do bot, bancos reais, migrations e
  `git diff --check` global (acusaria o whitespace do `.env.example` local).

### Riscos residuais e dependências

- Os campos reservados continuam aceitos: quem os ajusta não ganha proteção. Implementá-los depende de
  um pipeline de catálogo e de um chamador operacional (relacionado à T5/F-06).
- `affiliate_disclosure` é obrigatório e não é usado; a divulgação enviada é a do formatador.
- O guarda de deriva por regex pode não ver acessos por nomes de variável sem `config`.

## Histórico: T3 (integrada na main)

T3 integrada à `main` pelo merge `69b313737b7540c248f9d5e5298b921a7ebdd6a7`; resumo abaixo.

## T3 — entregas presas em SENDING (F-02, 12/11/2026)

Objetivo: entregas em `SENDING` com lease vencida devem ser identificadas e tratadas sem nunca serem
reenviadas nem declaradas enviadas. Escopo: só F-02. Nada de T4, deploy, migrations, operações live ou
envios pelo bot. Sem migration, sem mudança de esquema e sem mudança nos contratos de estados.

- Repositório: `Lipe-meira/promo_bot`.
- Branch: `codex/stale-delivery-recovery` (reutilizar ao continuar), criada de `origin/main` em
  `7ecb5af05975624d9d22cab94a03ee1ee3ee7525` (merge da T2.1).
- Commits: `8218153` (checkpoint da T2.1) e `6484a43` (implementação e testes). SHA verificado da
  implementação: `6484a43` (hash completo: `git rev-parse 6484a43`).
- Diff: [main...codex/stale-delivery-recovery](https://github.com/Lipe-meira/promo_bot/compare/main...codex/stale-delivery-recovery).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/stale-delivery-recovery`. Este
  checkpoint é um commit posterior ao da implementação; consulte seu SHA pelo Git.
- Merge na `main` autorizado explicitamente pelo usuário; o SHA do merge fica registrado no checkpoint
  da tarefa seguinte (`git log --merges -1 --format=%H` na `main`). A branch é preservada.
- **Limite operacional:** a recuperação vive em `DealDeliveryService`, que hoje não tem chamador no
  fluxo operacional; não existe worker periódico de outbox e esta tarefa não criou um. A recuperação
  automática em produção depende da integração futura do serviço. Os fluxos shadow não foram alterados.

### Causa-raiz

- `SENDING` só nasce em `DeliveryRepository.claim` (que grava `lease_until`). Saídas: `_finish`
  (exige `lease_until > agora`) e `mark_stale_sending_ambiguous` (`lease_until <= agora`).
- Se o dono não fecha a tentativa (processo encerrado, `CancelledError`, exceção fora de
  `RateLimited`/`RetryableBeforeSend`/`Ambiguous`/`Permanent`, ou falha ao gravar `SENT` depois de o
  Telegram aceitar), a lease vence, `claim` não escolhe a linha (portanto não há reenvio) e `_finish`
  levanta `DeliveryTransitionConflict`. A única saída restante era `mark_stale_sending_ambiguous`, que
  só um teste chamava. Resultado: linha eternamente em `SENDING`, sem rótulo de ambiguidade.
- A recuperação da T1 (`relay/queue.py`) trata mensagens-fonte, não entregas; não é afetada.
- Achado: `DealDeliveryService` não tem chamador de produção (só testes); nenhum código de produção
  cria entregas `PENDING` nem as envia. O defeito é do contrato do serviço e está latente hoje. Os
  caminhos shadow (`shadow_deliveries`, coin-shadow) têm tabelas próprias em que `sending` já
  significa `uncertain` por contrato e bloqueia reenvio; não foram alterados.

### Alterações e decisões

- `mark_stale_sending_ambiguous` foi **reutilizado sem alteração**: sua semântica atende (só
  `SENDING` com lease vencida; vai para `DELIVERY_AMBIGUOUS` com `DELIVERY_LEASE_EXPIRED_AMBIGUOUS`;
  mantém `attempt_count`/`last_attempt_at`; não define `sent_at` nem `telegram_message_id`).
- `delivery/service.py`: novo `DealDeliveryService.recover_stale_sending()` (conta e registra só o
  código e a quantidade, nunca dados da entrega) e `send()` o executa antes de ler a entrega, então
  todo uso do serviço fecha tentativas abandonadas. A varredura roda mesmo com gates fechados porque só
  aumenta a cautela.
- Não adotado: converter exceções não classificadas em `DELIVERY_AMBIGUOUS` imediatamente dentro de
  `send()` (mudaria o contrato de propagação; a lease já cobre o caso) e criar um worker/loop de
  outbox (fora do escopo; ver riscos).
- Estados/transições: só `SENDING → DELIVERY_AMBIGUOUS` (já previsto em `DELIVERY_TRANSITIONS`).
  `DELIVERY_AMBIGUOUS` continua exigindo revisão humana; `FAILED_PERMANENT` segue distinto.
- Documentação: `docs/ARCHITECTURE.md` (ciclo de vida de `SENDING` e recuperação).
- Skills usadas: `superpowers:systematic-debugging`, `superpowers:test-driven-development` e
  `superpowers:verification-before-completion`.

### Validações

Executadas em cópia limpa do commit de implementação `6484a43` (`git archive`):

- `uv lock --check --offline`, `ruff format --check .` (246 arquivos), `ruff check .` e `mypy src`
  (102 arquivos): aprovados.
- `pytest -m "not live and not browser"`: **1.416 aprovados, 5 excluídos, 6 avisos em 565,50 s**,
  exit 0 (referência da T2.1: 1.403; a diferença são os 13 testes novos). Sem falhas, inclusive do
  teste intermitente de timeout descrito nos riscos da T2.
- RED: `tests/unit/test_delivery_stale_recovery.py` antes da correção: 12 falhas e 1 passe. Três
  testes de comportamento mostravam a linha ficando em `SENDING` após a lease (envio interrompido,
  erro não classificado, gravação final perdida); nove falhavam por faltar `recover_stale_sending`; o
  controle de entrega já confirmada passava. GREEN: 13/13 e os 14 testes de `test_delivery_outbox.py`.
- Cobertura: processo interrompido, erro de transporte sem confirmação, `SENT` não gravado,
  fronteira exata da lease (`agora == lease_until` varre; um microssegundo antes não), remetente vivo
  não afetado, todos os outros estados intocados mesmo com lease antiga, idempotência, 6 varreduras
  concorrentes (soma = 1), remetente que perdeu a lease não sobrescreve a ambiguidade, `SENT`
  confirmado inalterado e nenhum reenvio em nenhum cenário (contagem de chamadas ao transporte).
- Mutações (cada uma derruba testes): `send()` sem varredura (3), sem filtro de lease (1), sem filtro de
  estado (6), `<` no lugar de `<=` (1), destino `PENDING` no lugar de `DELIVERY_AMBIGUOUS` (6).
- Não executados: testes live/browser, operações do bot, bancos reais, migrations e
  `git diff --check` global (acusaria o whitespace do `.env.example` local).

### Riscos de duplicação evitados e residuais

- Evitado: a linha nunca volta a `PENDING`/`FAILED_RETRYABLE`, `claim` só aceita esses dois estados,
  e nenhum caminho cria `SENT` sem `message_id` confirmado.
- Residual: sem worker de outbox e sem chamador do serviço no fluxo operacional, nada chama a varredura
  sozinho em produção; ela corre quando `send()` é usado ou quando um futuro worker a chamar.
- Residual: se um remetente vivo passar da lease e o Telegram aceitar, a linha fica ambígua e o
  `message_id` real é descartado (o fechamento tardio é recusado); é o lado seguro, sem reenvio, mas
  exige revisão humana.
- Residual: `DELIVERY_AMBIGUOUS → MANUAL_REVIEW → SENT/FAILED_PERMANENT` não tem implementação de
  produção (relacionado à T5/F-06).
- Residual: uma linha `SENDING` com `lease_until` nulo não é varrida; `claim` nunca a produz.

## Histórico: T2.1 (integrada na main)

Complemento da T2, antes da T3. A auditoria mostrou que links AliExpress já gravados eram checados na
entrega só por esquema e hostname (`urlsplit` descarta CR/LF/tab em silêncio), então uma prova ou
prévia persistida antes da T2 com controles, CRLF, espaços, bidi ou credenciais chegava ao transporte.
Escopo: apenas isso. Nada de T3, deploy, migrations reais, operações live ou envios pelo bot.

- Repositório: `Lipe-meira/promo_bot`.
- Branch: `codex/persisted-affiliate-link-validation` (reutilizar ao continuar), criada de
  `origin/main` em `16814423b290ac719f8b7a88dbf3d2ed9b844de5` (merge da T2).
- Commit de implementação verificado: `04e752a53abaef84a41ba72c065c0d12e5a256bd`.
- Diff: [main...codex/persisted-affiliate-link-validation](https://github.com/Lipe-meira/promo_bot/compare/main...codex/persisted-affiliate-link-validation).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/persisted-affiliate-link-validation`.
  Este checkpoint é um commit posterior ao da implementação; consulte seu SHA pelo Git.
- **Integrada na `main`** por merge commit `7ecb5af05975624d9d22cab94a03ee1ee3ee7525` (pais
  `16814423b290ac719f8b7a88dbf3d2ed9b844de5` e `4d1016c5ffd8e4c86b4270881dfae0aeb03897d6`) com
  autorização explícita do usuário. A branch `codex/persisted-affiliate-link-validation` foi preservada.
  Antes do merge, os dois caracteres invisíveis literais dos vetores de teste (U+202E e U+200B) viraram
  escapes; validação da árvore integrada: lockfile, ruff, mypy e 143 testes direcionados aprovados (a
  suíte completa, 1.403 aprovados, rodou antes na árvore equivalente).

### Alterações e decisões

- Reuso de `validate_official_https_link` (T2) com `PROMOTION_LINK_HOSTS` (`s.click.aliexpress.com`);
  nenhuma regra nova de formato.
- `affiliate/shadow_delivery.py`: `_require_valid_persisted_link` roda em `_validated_text` sobre
  `proof.short_link` e `preview.affiliate_link`, e no laço multi-link sobre cada prova, antes das
  checagens de correlação. Código fixo `SHADOW_LINK_INVALID`; o par esquema/host por `urlsplit` foi
  removido por ser subsumido. Falha antes de `get_chat`/`send_text` e a reserva durável continua
  bloqueando reenvio (`SHADOW_DELIVERY_ALREADY_ATTEMPTED`).
- `AliExpressEnrichmentService._validate_proof`: usa o validador; a mensagem do `ValueError` leva só o
  código (nunca a URL) e nada é persistido.
- `database/history_repository.py`: `validate_canonical_proof` (provider `aliexpress_official`) e
  `validate_coin_evidence` chamam `_require_valid_aliexpress_link` → `AffiliateHistoryError`
  `AFFILIATE_HISTORY_LINK_INVALID`. Cobre o caminho coin-shadow (simples e multi), cujo preview passa
  por `validate_preview` antes de reservar. O conjunto de hosts é local (o pacote `database` não
  importa providers; mesmo motivo do `coin_shadow.py`). Como os demais erros de histórico, ele sobe
  como exceção antes da reserva: nenhuma inspeção de canal, envio ou linha de entrega.
- Não alterado: histórico de provas (nada é reescrito nem regenerado), deduplicação, idempotência,
  gates e estados duráveis. Rejeição nunca gera novo link.
- Teste existente ajustado: `test_invalid_preview_rejected_before_network[link]` usava
  `https://other.invalid/e/test` esperando `SHADOW_PROOF_MISMATCH`; esse host agora cai antes em
  `SHADOW_LINK_INVALID` (coberto pelo caso `other-host` novo). O caso passou a usar um link bem formado
  e diferente (`https://s.click.aliexpress.com/e/other-fixture`) para continuar exercitando o mismatch.
- Skills usadas: `superpowers:systematic-debugging`, `superpowers:test-driven-development`;
  `verification-before-completion` na conferência final.

### Validações

Executadas em cópia limpa do commit `04e752a` (`git archive`), para não herdar as alterações locais de
exemplo:

- `uv lock --check --offline`, `ruff format --check .` (245 arquivos), `ruff check .` e
  `mypy src` (102 arquivos): aprovados.
- `pytest -m "not live and not browser"`: **1.403 aprovados, 5 excluídos, 6 avisos em 616,56 s**
  (os 6 avisos de sempre). Referência da T2: 1.354; a diferença são os 49 testes novos. Nenhuma falha
  nesta execução, inclusive do teste intermitente de timeout descrito nos riscos da T2.
- Testes novos em `tests/unit/test_persisted_affiliate_link_validation.py`: 18 formatos inválidos no
  link único (LF, CRLF, LF final, tab, espaço, espaço final, NUL, bidi, largura zero, credenciais,
  http, porta, porta inválida, fragmento, só `/`, escape inválido, host parecido, > 2.048),
  links legítimos com parâmetros de tracking preservados byte a byte, multi-link com link inválido
  no primeiro e no segundo, coin-shadow, e enriquecimento sem persistir nada. Antes da correção,
  47 dos 49 falhavam (o link com LF chegava a `sent`); os 2 restantes são o controle legítimo e
  `other-host` no enriquecimento. Quatro mutações (sem checagem no link único, no laço multi-link, no
  histórico coin, no enriquecimento) derrubam cada uma ao menos um teste.
- Não executados: testes live/browser, qualquer comando operacional do bot, bancos reais, migrations e
  `git diff --check` global (acusaria o whitespace do `.env.example` local).

### Riscos residuais da T2.1

- Só AliExpress: provas Shopee/Mercado Livre persistidas e `delivery/service.py` (outbox de produção,
  que só compara `deal.affiliate_link == proof.short_link`) não foram tocados.
- A prova só é validada quando usada; linhas ruins permanecem no banco (por decisão: não reescrever
  histórico). Uma prévia inválida nunca é reparada automaticamente; é preciso preparar outra.
- Limite de 2.048 caracteres do contrato passa a bloquear prévias antigas com links maiores (nenhum
  caso real conhecido).
- `security/aliexpress_short_links._has_unsafe_url_text` segue duplicando a checagem de caracteres
  (herdado da T2).

## Histórico: T2 (integrada na main)

Corrigir a T2 da auditoria técnica de 12/11/2026, exclusivamente F-04 e F-05b: validação estrita
das URLs que entram no bot. F-04: os validadores do link afiliado devolvido pela API oficial
aceitavam quebra de linha, espaços, caracteres de controle, bidi e texto adicional, porque só
inspecionavam o resultado de `urlsplit`, que descarta tabs e quebras de linha em silêncio. F-05b:
`SafeUrlExpander` validava a URL inicial e cada `Location` só depois de `urlsplit`/`urljoin`.
Gates, contratos de entrega, deduplicação, estados duráveis e os demais achados não foram alterados.

- Repositório: `Lipe-meira/promo_bot`.
- Branch da tarefa: `codex/affiliate-url-validation-hardening` (preservada).
- Base: `origin/main` em `c628d632be90602913ce1859f89be723efa19e82` (merge da T1).
- Último SHA verificado antes deste registro (implementação):
  `406d01c76439607f55d2d8cc594a0fd5d241aeaf`; o commit de F-04 é
  `182f5dd7eb1a0a9d5ce103f936fcf11481d94998`.
- Diff: [main...codex/affiliate-url-validation-hardening](https://github.com/Lipe-meira/promo_bot/compare/main...codex/affiliate-url-validation-hardening).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/affiliate-url-validation-hardening`.
  Este registro é um commit posterior ao da implementação; consulte seu SHA pelo Git.
- Integrada na `main` pelo commit `16814423b290ac719f8b7a88dbf3d2ed9b844de5`.

### T2 — alterações e decisões

- Novo `src/promo_bot/security/link_validation.py` (módulo-folha, sem imports do projeto):
  `validate_official_https_link` valida em camadas: caracteres brutos devem ser de URI estrito
  (RFC 3986, com escapes percentuais bem formados) e no máximo 2.048; depois HTTPS, host exato da
  lista do chamador, ausência de credenciais, fragmento e porta diferente de 443, e caminho real
  (mais que `/`). `has_forbidden_url_characters` rejeita controles, separadores, espaços e invisíveis
  (bidi, largura zero); letras não ASCII comuns passam.
- Usado pelo parser canônico (`parsing.py`), pelo parser coin-shadow (`coin_shadow.py`) e pela
  checagem de reutilização do cache (`aliexpress_conversion._is_valid_affiliate_link`), cada um com
  o seu código de erro e o seu conjunto de hosts. O conjunto do coin-shadow continua local porque o
  teste de isolamento proíbe os módulos canônicos de importá-lo e o coin deve seguir independente.
- Decisões de contrato: link devolvido com fragmento ou caminho só `/` agora é rejeitado em todos os
  caminhos (antes só o cache rejeitava fragmento); porta 443 explícita é aceita em todos (antes o
  cache a rejeitava); espaços nas extremidades do valor da API continuam sendo removidos antes da
  validação; a query, inclusive percent-encoded, é preservada sem reescrita.
- `SafeUrlExpander`: a URL inicial e o cabeçalho `Location` crus são checados antes de
  `urlsplit`/`urljoin`, DNS e requisição (`INVALID_URL` e `INVALID_REDIRECT_URL`), com limite de 4.096;
  hostname não ASCII é rejeitado e a porta 0 deixou de valer como porta padrão. Allowlist, regras de
  redirecionamento, validação de IP e do peer ficam intactas; letras não ASCII em caminhos continuam
  permitidas para não quebrar redirecionamentos reais.
- Documentação: `docs/ARCHITECTURE.md` (Segurança de URLs) e `docs/ALIEXPRESS_AFFILIATE.md` (contrato
  do link devolvido).
- Armadilha registrada: ao gravar arquivos, as sequências `\uXXXX` digitadas viraram caracteres
  literais invisíveis no código-fonte (inclusive bidi); foram convertidas de volta em escapes e o
  `RUF001` do ruff acusa o problema. Conferir caracteres não ASCII antes de commitar testes com Unicode.
- Skills usadas: `superpowers:systematic-debugging` (defeitos reproduzidos na auditoria),
  `superpowers:test-driven-development` (testes vermelhos antes da correção; mutações verificadas) e
  `superpowers:verification-before-completion`.

### T2 — validações

Executadas em cópia limpa do commit de implementação `406d01c` (`git archive`), para não herdar as
alterações locais de exemplo:

- `uv lock --check --offline`: aprovado (41 pacotes).
- `uv run --offline ruff format --check .`: aprovado (244 arquivos).
- `uv run --offline ruff check .`: aprovado.
- `uv run --offline mypy src`: aprovado (102 arquivos).
- `uv run --offline pytest -m "not live and not browser"`: duas execuções completas da mesma
  árvore. A primeira terminou com **1.353 aprovados e 1 falha** em
  `test_aliexpress_short_links.py::test_mobile_hop_transport_pins_ip_but_preserves_mobile_host_for_tls_and_http`
  (teste intermitente preexistente, ver riscos residuais). A segunda terminou com **1.354 aprovados,
  5 excluídos e 6 avisos em 497,15 s**. Referência da `main` após a T1: 1.188 aprovados; a diferença
  são os 166 testes novos. Os 6 avisos são os de sempre (ciclos de chaves estrangeiras do SQLAlchemy
  e adaptador datetime padrão do SQLite).
- Testes novos: `tests/unit/test_affiliate_link_validation.py` (uma tabela de vetores aplicada aos
  três validadores), 11 casos do expansor em `test_url_security.py` mais os de autoridade,
  `Location` e preservação, e um fluxo de conversão ponta a ponta. Antes da correção, 91 desses
  testes falhavam pelo defeito real; os demais já passavam e são guardas de regressão (userinfo,
  portas, esquemas, hosts parecidos). Sete mutações verificadas (sem checagem de caracteres brutos,
  espaço não proibido, `Location` checado após `urljoin`, porta 0 como padrão, host não ASCII
  aceito, fragmento aceito, cache sempre válido): cada uma derruba ao menos um teste.
- `git diff --cached --check` aprovado nos dois commits; conferido que as linhas adicionadas só
  contêm letras acentuadas visíveis (nenhum caractere invisível ou bidi).
- Não executados: testes live/browser, geração real de links, qualquer comando operacional do bot,
  bancos reais e `git diff --check` global (acusaria o whitespace do `.env.example` local).

## Histórico: T1 concluída e integrada

T1 (F-01, supervisão dos workers e do loop de recuperação da fila durável) foi autorizada e integrada
na `main` por merge commit `c628d632be90602913ce1859f89be723efa19e82` (pais `d81dcdf` e `e64a6ad`;
commits `6036b4c` e `e64a6ad`). A autorização de merge foi explícita do usuário e a validação foi
refeita na árvore integrada (1.188 aprovados, 5 excluídos, 6 avisos). A branch
`codex/relay-worker-supervision` foi preservada. Resumo técnico: workers e recuperação registram só
código estável e tipo da exceção, liberam o id, chamam `task_done`, fazem backoff exponencial com teto
de 30 s e nunca marcam a mensagem como processada; o estado durável conduz a recuperação.

## Auditoria de 12/11/2026: tarefas ainda não iniciadas

O relatório completo ficou no chat; resumo para retomada (T1 a T4 integradas; T5A em revisão na branch `codex/runtime-migration-safety`):

- T3 — F-02: integrada à `main` (merge `69b313737b7540c248f9d5e5298b921a7ebdd6a7`).
- T4 — F-03: implementada na branch `codex/config-policy-contract-hardening`, aguardando revisão e merge
  (ver a seção T4 acima).
- T5A — F-07: implementada (ver seção T5A). T5B — F-06 (pendente): máquinas de estado do domínio sem
  uso em produção.
- T6 — F-08, F-09 e F-10: retry do `send-test --live`, `except: pass` silencioso em
  `coin_shadow_generation`, `doctor` com `"phase": 2` fixo, ausência de CI e documentação histórica.
- F-05a (transporte com IP fixo no expansor genérico) é opcional e maior.

## Riscos residuais da T2

- (Resolvido na T2.1) Provas persistidas passaram a ser revalidadas antes do uso.
- `security/aliexpress_short_links._has_unsafe_url_text` duplica a ideia de checagem de caracteres
  brutos (sem rejeitar espaço); foi mantido para não alterar um módulo fora do escopo.
- Validadores de link de outras lojas (Shopee, Mercado Livre) não foram revisados nesta tarefa.
- Teste intermitente preexistente, fora do escopo da T2 e não corrigido aqui:
  `test_mobile_hop_transport_pins_ip_but_preserves_mobile_host_for_tls_and_http` exige
  `delegate.calls == [("8.34.34.34", 443, 2)]`, mas `PinnedAliExpressNetworkBackend.connect_tcp`
  desconta o tempo decorrido do timeout e o relógio do Windows avança em ticks de cerca de 16 ms,
  gerando `1.984`. Medido repetindo o teste: 13 falhas em 15.000 na `main` `c628d63` e 4 em 15.000 na
  árvore da T2, sempre na mesma asserção. Correção sugerida para uma tarefa própria: afirmar um
  intervalo (`<= 2` e próximo de 2) em vez de igualdade exata.

## Pendências e contexto herdado

- Mudanças preexistentes em `.env.example` e `config.example.yaml` permanecem locais, preservadas e
  fora dos commits. O placeholder de destino privado do exemplo local viola o schema
  (`^-100[1-9][0-9]*$`) e reproduz 10 falhas de teste se a suíte rodar nesse checkout; por isso a
  suíte completa é validada em cópia limpa do commit. Não divulgar seus valores.
- O README ainda registra a pendência de correção documental do roteiro anterior do piloto.
- Limitação conhecida: não foi reproduzido um lock real de SQLite entre processos.
