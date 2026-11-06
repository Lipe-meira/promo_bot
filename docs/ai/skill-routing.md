# Roteamento contextual de skills

Leia o catálogo de skills e ferramentas exposto pela sessão; disponibilidade muda
entre máquinas e chats. Abra o `SKILL.md` relevante antes de aplicar a skill e
informe seu uso. Não presuma instalação por existir uma referência neste documento.
Não carregue todas as skills nem instale plugins automaticamente.

| Natureza da tarefa | Prática / skill quando disponível |
| --- | --- |
| Design, novo comportamento ou decisão complexa | `superpowers:brainstorming`: esclarecer requisitos e alternativas antes de implementar |
| Mudança com várias etapas e dependências | `superpowers:writing-plans`; registrar plano no padrão de `docs/superpowers/plans/` |
| Executar plano aprovado e atual | `superpowers:executing-plans`; confirmar que escopo e restrições ainda se aplicam |
| Funcionalidade ou correção de código | `superpowers:test-driven-development` quando apropriado; testar comportamento e fronteiras, sem testes que só espelham implementação |
| Bug ou falha inesperada | `superpowers:systematic-debugging`; reproduzir, reunir evidências e corrigir a causa |
| Antes de declarar conclusão | `superpowers:verification-before-completion`; executar e ler as verificações antes de afirmar resultados |
| Interface real, usabilidade ou revisão visual | `impeccable` e skills específicas de UX disponíveis; não se aplicam automaticamente à CLI ou documentação |
| Exploração ou impacto de código | GitNexus, se disponível e com índice adequado; confirmar resultados no código atual |
| GitHub, contratos ou outra integração | Plugin pertinente somente se necessário e disponível; respeitar autorização e gates do projeto |

Se uma skill estiver ausente, registre a limitação e use a prática equivalente com
as ferramentas existentes, sem inventar comandos ou dependências. Se o usuário
exigir especificamente uma skill indispensável e ela não puder ser localizada,
informe o bloqueio. Skills não ampliam autorização para produção ou operações live.

Na sessão de implantação deste padrão (06/11/2026), o catálogo oferecia Superpowers
(incluindo as práticas da tabela), Impeccable, skills GitNexus e ferramentas GitHub,
Figma, Supabase e Context7, entre outras. Isso é um inventário observado, não uma
garantia de conexão, autenticação, índice ou disponibilidade futura. Esta tarefa
documental utilizou `using-superpowers`, `verification-before-completion` e
`systematic-debugging` para investigar uma falha na configuração local de exemplo;
não precisou de ferramentas de interface, banco ou integrações do bot.

A documentação especializada continua sendo a fonte de verdade: a aplicação de
uma skill deve ser proporcional à tarefa e respeitar [AGENTS.md](../../AGENTS.md).
