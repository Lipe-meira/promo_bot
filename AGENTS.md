# Instruções permanentes para o Codex

## Contexto e fontes de verdade

Bot local de promoções em Python 3.12, assíncrono, com SQLite, SQLAlchemy/Alembic,
Pydantic, Telethon para leitura e Telegram Bot API para entrega. Use o `uv` e as
dependências de `pyproject.toml`/`uv.lock`; não introduza ferramentas sem necessidade.
Leia [README.md](README.md) para o estado implementado e os limites atuais;
[arquitetura](docs/ARCHITECTURE.md), [produto](docs/PRODUCT_SPEC.md) e contratos
especializados em `docs/` explicam as decisões. Seções históricas por fase não
autorizam capacidades ou operações live atuais. Preserve `docs/superpowers/`;
planos antigos não são autorização para executar operações hoje.

Em `src/promo_bot/`: `domain/` contém tipos e estados independentes de infraestrutura;
`config/` valida configurações; `relay/` ingere, persiste e enfileira;
`providers/` concentra contratos e adapters; `affiliate/` trata geração, provas e
shadow; `discovery/` busca e classifica; `delivery/` controla outbox;
`database/` persiste dados; `security/` valida URLs; `telegram/` isola transportes;
`observability/` sanitiza logs; `cli.py` compõe os comandos. Migrações ficam em
`migrations/` e testes em `tests/`.

## Início e organização da tarefa

- Confirme diretório seguro de desenvolvimento, repositório, remoto, branch e
  `git status` antes de editar. Procure instruções aplicáveis, inclusive ignoradas.
- Leia o [checkpoint](docs/continuity/backend.md) e os documentos da área afetada.
  Preserve alterações e arquivos preexistentes; não os inclua nos seus commits.
- Faça mudanças mínimas e coesas, mantendo domínio, infraestrutura e transportes
  separados. Não simule sucesso real em providers indisponíveis.
- Use o [roteamento contextual de skills](docs/ai/skill-routing.md). Carregue apenas
  instruções relevantes e confirme a disponibilidade na sessão atual.

## Política obrigatória de Git e rastreabilidade

Esta política vale para todas as futuras tarefas do Codex neste projeto, inclusive
documentais. Criar e publicar a branch faz parte do fluxo padrão e não depende de
o usuário repetir a instrução.

1. Toda nova tarefa de implementação usa branch própria, preferencialmente
   `codex/<tarefa>`, criada a partir da base apropriada e atualizada. Faça fetch do
   remoto e confirme a base antes de começar; se indisponível, registre a limitação.
   Não altere arquivos nem faça commits diretamente na `main`.
2. Ao continuar a mesma tarefa em outro chat, reutilize a branch existente indicada
   no checkpoint. Confira o remoto e as mudanças locais antes de retomá-la; não crie
   outra branch desnecessariamente nem sobrescreva trabalho concorrente.
3. Durante o desenvolvimento, crie commits coerentes com Conventional Commits
   (`docs:`, `feat:`, `fix:`, `test:`, `refactor:`, `chore:`). Revise o diff e adicione
   explicitamente só os arquivos da tarefa, sem segredos ou mudanças alheias.
4. Em tarefas com várias etapas relevantes, publique checkpoints intermediários
   coerentes quando facilitarem a revisão, com commits e `git push` da branch.
5. Ao concluir, execute as validações aplicáveis, registre os resultados e publique
   a branch no GitHub com `git push` (no primeiro envio, configure o upstream).
   Confirme que o SHA remoto corresponde ao commit entregue.
6. Sempre informe o nome da branch, o SHA completo do commit e um link de diff ou
   Pull Request no GitHub. Se não puder publicar, explique o motivo e liste os
   commits que ficaram somente locais; não apresente publicação como concluída.
7. Nunca faça merge na `main` sem autorização explícita. Não use force push nem
   reescreva histórico compartilhado. Não faça reset destrutivo ou limpeza de
   arquivos sem autorização.
8. Registre branch, último SHA confirmado, alterações e estado da publicação no
   [checkpoint de continuidade](docs/continuity/README.md). Identifique o commit
   verificado antes de salvar o checkpoint; o SHA do próprio commit que o inclui
   é consultado pelo Git, evitando autorreferência impossível.

O objetivo é disponibilizar alterações no GitHub para outros agentes inspecionarem
commits, diferenças e código. Autorização para publicar a branch não autoriza
deploy, publicação de mensagens pelo bot ou alteração de produção.

## Segurança e limites de autonomia

- Inspeções, documentação, edições no escopo, validações offline isoladas, commits
  e push da branch estão autorizados. Peça autorização para ações destrutivas,
  sensíveis ou fora do escopo; merge e deploy sempre exigem autorização explícita.
- Não execute implicitamente autenticação, chamadas externas do bot, listeners,
  publicação real ou envios privados. Preserve gates Telegram, AliExpress, Shopee
  e Mercado Livre. `DRY_RUN=true` não bloqueia toda entrega shadow;
  `uv --offline` não bloqueia a rede do programa.
- Não modifique sessões Telethon, `.env`, `config.yaml`, `.private/` nem bancos do
  usuário. Não execute migrations em bancos reais sem autorização explícita.
  Separe shadow e produção; testes usam bancos temporários e transportes falsos.
- Nunca versionar tokens, credenciais, sessões, dados pessoais, respostas live ou
  URLs afiliadas sensíveis, inclusive em checkpoints, logs e exemplos.
  `AGENTS.local.md` é ignorado para orientações privadas; não copie conteúdo privado
  para o `AGENTS.md` compartilhado. Preserve instruções locais existentes.
- Preserve deduplicação, persistência antes do envio, validação de URLs e bloqueio
  de entregas ambíguas. Tracking confirmado não comprova comissão. Preço, cupom,
  atribuição e recursos indisponíveis seguem os limites do README e dos contratos.

## Validação e conclusão

Reutilize as ferramentas configuradas. Para mudanças de código, execute as
verificações aplicáveis e os testes afetados; a suíte offline é a referência:

```powershell
uv lock --check --offline
uv run --offline ruff format --check .
uv run --offline ruff check .
uv run --offline mypy src
uv run --offline pytest -m "not live and not browser"
git diff --check
```

Não execute testes live/browser, nem use bancos reais, para validar documentação.
Para mudanças apenas documentais, confira links locais, coerência com contratos,
diff e regras de ignore; justifique verificações omitidas. Nunca declare aprovação
sem executar: separe resultados aprovados, falhos, indisponíveis e não executados.

Antes da entrega, revise o escopo e a ausência de dados sensíveis, atualize o
checkpoint, valide e publique a branch. Entregue resumo, arquivos, resultados,
limitações, branch, SHA e link de revisão. Pendências e falhas de publicação devem
ficar explícitas para o próximo agente.
