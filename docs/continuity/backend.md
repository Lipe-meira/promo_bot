# Checkpoint backend — 06/11/2026

## Objetivo e estado

Padronizar desenvolvimento assistido por IA e tornar obrigatória a política de
branches e rastreabilidade. Escopo exclusivamente documental; comportamento do bot,
gates, código, migrations e configurações operacionais não foram alterados.

- Repositório: `Lipe-meira/promo_bot`.
- Branch da tarefa: `codex/development-standards` (reutilizar ao continuar esta tarefa).
- Base atualizada por `git fetch origin`: `origin/main`.
- Último SHA confirmado antes deste registro:
  `c7c26904044a6f87275d7a5b84b53bd68b301b71` (implementação documental).
- SHA da base: `24093432786b8e33f2284cb8520bed889d4d161a`.
- Diff: [main...codex/development-standards](https://github.com/Lipe-meira/promo_bot/compare/main...codex/development-standards).
- Publicação: commit de implementação acima publicado com `git push -u origin
  codex/development-standards`; SHA confirmado por `git ls-remote`. Este registro
  final é um commit posterior, cujo SHA deve ser consultado pelo Git.

## Alterações e decisões

- Criado `AGENTS.md`: arquitetura, segurança, validação, autonomia, Git obrigatório
  e referências às fontes existentes.
- `.gitignore` libera somente o `AGENTS.md` da raiz; mantém instruções aninhadas
  ignoradas e acrescenta `AGENTS.local.md` para contexto privado. Nenhum `AGENTS.md`
  preexistente foi encontrado na inspeção, inclusive entre arquivos ignorados.
- Criados roteamento contextual de skills e mecanismo compacto de continuidade;
  README aponta para esses documentos. Planos e especificações antigos preservados.
- Skills usadas: `using-superpowers`, `verification-before-completion` e
  `systematic-debugging` para diagnosticar falha causada pela configuração local.
  Disponibilidade observada e regras de seleção em `docs/ai/skill-routing.md`.
- O SHA do commit que contém este arquivo deve ser consultado pelo Git conforme
  `docs/continuity/README.md`, sem tentar registrar seu próprio hash.

## Validações

- `uv lock --check --offline`: aprovado (41 pacotes).
- `uv run --offline ruff format --check .`: aprovado (240 arquivos).
- `uv run --offline ruff check .`: aprovado.
- `uv run --offline mypy src`: aprovado (101 arquivos).
- `uv run --offline pytest -m "not live and not browser"`: 1.167 aprovados,
  10 falhos, 5 excluídos e 6 avisos em 534,52 segundos. As dez falhas em
  `test_aliexpress_cli_and_formatter.py`, `test_cli.py` e `test_config.py` reportam
  a mesma configuração de destino privado inválida no arquivo de exemplo local
  preexistente. A suíte completa não está aprovada neste checkout.
- Avisos: ciclos de chaves estrangeiras na ordenação de tabelas pelo SQLAlchemy e
  depreciação do adaptador datetime padrão do SQLite; fora do escopo documental.
- Revisão: 28 links locais válidos; `git diff --cached --check` aprovado para os
  seis arquivos da tarefa; ignore da raiz e instruções privadas verificados.
- O arquivo de testes `test_aliexpress_cli_and_formatter.py` falhou com a
  configuração de exemplo modificada localmente (destino privado fora do padrão
  de validação). Os mesmos quatro testes passaram ao usar uma cópia temporária
  do `config.example.yaml` versionado em HEAD, sem alterar o arquivo do usuário.
- `git diff --check` global encontrou whitespace preexistente em `.env.example:13`.
  Não corrigido por ser trabalho alheio; verificar separadamente o diff da tarefa.
- Nenhum teste live/browser, acesso a serviços do bot ou banco real foi executado.

## Entrega e pendências de retomada

- Documentação revisada e publicada; checkpoint final deve acompanhar a mesma
  branch. Para entrega atual, confirmar `HEAD` contra o SHA remoto.
- Uma futura tarefa pode corrigir a configuração de exemplo local com autorização
  do usuário; não transportar valores locais para o repositório nem afirmar suíte
  integralmente aprovada com base apenas na reprodução isolada de quatro testes.
- Mudanças preexistentes em `.env.example` e `config.example.yaml` permanecem
  locais, preservadas e fora dos commits desta tarefa. Não divulgar seus valores.
- O README já registra uma pendência de correção documental do roteiro anterior
  do piloto; permanece fora deste escopo, sem autorização para executar o piloto.
- Na tarefa documental inicial não havia autorização de merge. Em 06/11/2026,
  o usuário autorizou explicitamente integrar esta branch e fazer push na `main`,
  condicionado à comprovação da origem das falhas e ausência de regressões ou
  conflitos não triviais. Essa autorização não se estende a merges futuros,
  deploy, migrations reais, operações live ou envios pelo bot.

## Auditoria para integração na main — 06/11/2026

- Main remota conferida após fetch:
  `24093432786b8e33f2284cb8520bed889d4d161a`.
- Branch documental publicada e auditada:
  `e09a91adb30edbd72d24365b1815d4faa05181b6`.
- Diff revisado: apenas `.gitignore`, `AGENTS.md`, `README.md`,
  `docs/ai/skill-routing.md`, `docs/continuity/README.md` e este checkpoint.
  Código, testes, migrations, exemplos versionados e dependências idênticos.
- `git merge-tree --write-tree` concluiu sem conflitos; a main é ancestral da
  branch documental. A integração deve manter a branch original para consulta.
- Diagnóstico controlado em cópias temporárias isoladas, sem arquivos privados:
  na main limpa, os três arquivos de testes envolvidos tiveram 46 aprovações;
  substituindo somente `config.example.yaml` pela versão local preexistente,
  houve exatamente as mesmas 10 falhas e 36 aprovações. Todas as falhas apontam
  formato inválido de `telegram_shadow_delivery.allowed_destinations.private-test.chat_id`.
  Isso comprova que as falhas independem das novas alterações documentais.
- Lockfile offline, Ruff (formatação e lint), mypy e 28 links locais aprovados
  na cópia limpa da branch. A suíte offline completa terminou com **1.177
  aprovados, 5 excluídos e 6 avisos em 564,53 segundos**. Os avisos continuam sendo
  os ciclos de chaves estrangeiras do SQLAlchemy e o adaptador datetime do SQLite.
  Nenhuma falha ou regressão encontrada nessa execução isolada.
- SHA-256 dos dois arquivos locais de exemplo verificado antes e durante a
  auditoria: conteúdo integralmente preservado. Nenhum deles integra o diff.
- Skills aplicadas à integração: `systematic-debugging`, `using-git-worktrees`,
  `verification-before-completion` e `finishing-a-development-branch`. A escolha de
  merge e preservação da branch segue a autorização explícita do usuário.
- Este checkpoint complementa o resultado histórico do checkout local acima:
  o repositório versionado está aprovado; as configurações locais continuam
  preservadas e podem reproduzir as dez falhas se usadas para executar testes.
- Publicar este registro na branch original antes de integrar. O commit de merge
  descendente na `main` deve registrar a autorização e a validação; consultar seu
  SHA pelo histórico Git, sem tentar incluir neste arquivo seu próprio hash.
- Novas tarefas continuam exigindo branches próprias, publicação para revisão e
  autorização explícita antes de qualquer novo merge, conforme `AGENTS.md`.
