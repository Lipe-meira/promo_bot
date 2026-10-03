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
exige schema `b8c2e4f6a901`, inclusive invocações singleton antigas. Banco anterior
é recusado com `AFFILIATE_HISTORY_SCHEMA_REQUIRED` antes dos transportes, sem
migration automática. Seus comandos continuam iguais após upgrade explícito ou
seleção de banco novo migrado. Auditoria read-only continua disponível no banco
anterior. Não foi implementado ORM paralelo para duas versões de schema.

## Verificação final

Após a correção da revisão:

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
