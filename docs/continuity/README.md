# Continuidade entre chats

As regras permanentes ficam em [AGENTS.md](../../AGENTS.md), e os contratos e o
estado funcional no [README](../../README.md) e nos guias de `docs/`.
O [checkpoint backend](backend.md) guarda apenas o contexto transitório útil para
retomar o trabalho. Planos e especificações continuam em `docs/superpowers/`.

Ao iniciar outro chat, leia o checkpoint, confirme `git status`, branch, remoto e
commits locais/remotos. Retome a branch da mesma tarefa. Não assuma que o checkout
atual, o checkpoint ou um resultado antigo de teste representa o estado remoto.

Atualize o checkpoint ao interromper, concluir ou atingir uma etapa relevante:

- Data, objetivo, escopo e estado da tarefa.
- Branch, base, último SHA completo verificado e link de diff/PR.
- Publicação: último SHA confirmado no remoto ou commits somente locais e motivo.
- Alterações, decisões e justificativas necessárias à retomada.
- Validações executadas, resultados, limitações e verificações não executadas.
- Problemas conhecidos, pendências e próximos passos.

O SHA registrado é o último commit confirmado **antes** de salvar o checkpoint.
O commit que contém o checkpoint não pode registrar seu próprio hash; consulte
`git log -1 --format=%H -- docs/continuity/backend.md` para localizá-lo e
`git rev-parse HEAD` / `git ls-remote origin refs/heads/<branch>` para verificar a
entrega atual. Um commit final apenas de checkpoint pode referenciar o commit da
implementação já publicado, sem criar uma sequência infinita de atualizações.

Mantenha um registro compacto por frente ativa; substitua contexto obsoleto em vez
de acumular transcrições. Use o histórico Git para detalhes. Nunca registre tokens,
credenciais, sessões, dados pessoais ou URLs afiliadas sensíveis.
