# Checkpoint backend — 12/11/2026

## Objetivo e estado

Corrigir a T1 (F-01) da auditoria técnica de 12/11/2026: workers e loop de recuperação da fila
durável (`DurableRelayQueue`) encerravam silenciosamente após uma exceção inesperada, deixando
mensagens sem processamento até reiniciar o processo. Escopo: `src/promo_bot/relay/queue.py`,
testes offline e uma nota em `docs/ARCHITECTURE.md`. Gates, estados duráveis, migrations,
configuração e os demais achados da auditoria não foram alterados.

- Repositório: `Lipe-meira/promo_bot`.
- Branch da tarefa: `codex/relay-worker-supervision` (reutilizar ao continuar esta tarefa).
- Base: `origin/main` em `d81dcdf3aa7af54530f2e8193acec9a6b76a06eb`.
- Último SHA verificado antes deste registro (implementação):
  `6036b4cc7db4d47a44952d86e7968478caddff22`.
- Diff: [main...codex/relay-worker-supervision](https://github.com/Lipe-meira/promo_bot/compare/main...codex/relay-worker-supervision).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/relay-worker-supervision`. Este
  registro é um commit posterior ao da implementação; consulte seu SHA pelo Git.

## Alterações e decisões

- `_worker`: captura `Exception` de `processor.process`, registra somente o código estável
  `RELAY_WORKER_UNEXPECTED_ERROR` e o tipo da exceção (erros de driver podem embutir valores SQL,
  como links), libera o id de `_enqueued`, sempre chama `task_done` e aguarda backoff exponencial
  antes do próximo item. O backoff começa em `retry_initial_seconds`, tem teto de 30 s e volta ao
  início após um sucesso. `CancelledError` continua propagando.
- `_recovery_loop`: backfill de candidatos e `recover_once` rodam em passos independentes e
  protegidos (`RELAY_RECOVERY_UNEXPECTED_ERROR`); falhas consecutivas ampliam o intervalo com o mesmo
  backoff e um sucesso restaura o intervalo normal.
- `DurableRelayQueue` aceita `sleep` injetável (somente para testes determinísticos).
- A falha nunca marca a mensagem como processada nem repete operação externa: o estado durável
  (`RECEIVED`, `FAILED_RETRYABLE` ou lease de `PROCESSING` vencida) é o que `recover_once` consulta,
  com os limites de tentativas já existentes. Nenhum retry imediato foi acrescentado.
- Mantido sem alteração: a recuperação inicial dentro de `start()` continua falhando de forma
  explícita (fail-fast), pois o processo ainda não está operando.
- Skills usadas: `superpowers:systematic-debugging` (defeito reproduzido na auditoria),
  `superpowers:test-driven-development` (testes vermelhos antes da correção; mutações verificadas) e
  `superpowers:verification-before-completion`.

## Validações

Executadas em cópia limpa do commit de implementação (`git archive`), para não herdar as
alterações locais de exemplo:

- `uv lock --check --offline`: aprovado (41 pacotes).
- `uv run --offline ruff format --check .`: aprovado (242 arquivos).
- `uv run --offline ruff check .`: aprovado.
- `uv run --offline mypy src`: aprovado (101 arquivos).
- `uv run --offline pytest -m "not live and not browser"`: **1.188 aprovados, 5 excluídos e 6
  avisos em 481,27 s**. Referência da `main` na auditoria: 1.177 aprovados; a diferença são os 11
  testes novos. Os 6 avisos são os de sempre (ciclos de chaves estrangeiras do SQLAlchemy e adaptador
  datetime padrão do SQLite).
- `tests/unit/test_relay_queue_supervision.py` (11 testes): 9 falhavam antes da correção, pelo
  defeito real (`join()` travado ou condição nunca atingida, pois worker e recuperação morriam);
  2 são guardas de regressão que já passavam (cancelamento e parada). Mutações verificadas: sem
  backoff no worker, passos de recuperação acoplados, log com o texto da exceção e id não
  liberado em falha; cada uma derruba ao menos um teste.
- `git diff --cached --check` aprovado para os três arquivos da implementação.
- Não executados: testes live/browser, qualquer comando operacional do bot, bancos reais e
  `git diff --check` global (acusaria o whitespace do `.env.example` local preexistente).

## Auditoria de 12/11/2026: tarefas ainda não iniciadas

O relatório completo ficou no chat; resumo para retomada (P2 = F-01, o restante P3):

- T2 — F-04 e F-05b: validar com regex estrita o link afiliado devolvido pela API (hoje aceita
  quebra de linha, espaço e bidi) e rejeitar caracteres de controle em `SafeUrlExpander`.
- T3 — F-02: `DeliveryRepository.mark_stale_sending_ambiguous` não é chamado por código de produção.
- T4 — F-03: campos de política de `config.example.yaml` (`blacklist`, `blocked_sellers`,
  `maximum_price`, limites por hora, `templates` etc.) são validados, mas nenhum código os lê.
- T5 — F-06 e F-07 (exigem investigação): máquinas de estado do domínio sem uso em produção;
  `listen` migra o banco runtime sem backup e o README não descreve isso.
- T6 — F-08, F-09 e F-10: retry do `send-test --live`, `except: pass` silencioso em
  `coin_shadow_generation`, `doctor` com `"phase": 2` fixo, ausência de CI e documentação histórica.
- F-05a (transporte com IP fixo no expansor genérico) é opcional e maior.

## Pendências e contexto herdado

- Mudanças preexistentes em `.env.example` e `config.example.yaml` permanecem locais, preservadas e
  fora dos commits. O placeholder de destino privado do exemplo local viola o schema
  (`^-100[1-9][0-9]*$`) e reproduz 10 falhas de teste se a suíte rodar nesse checkout; por isso a
  suíte completa é validada em cópia limpa do commit. Não divulgar seus valores.
- Nenhuma autorização de merge foi dada para esta tarefa, nem para deploy, migrations reais,
  operações live ou envios pelo bot.
- O README ainda registra a pendência de correção documental do roteiro anterior do piloto.
- Limitação conhecida: não foi reproduzido um lock real de SQLite entre processos; o gatilho foi
  simulado com exceções injetadas em processadores falsos e em `recover_once`.
