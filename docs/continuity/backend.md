# Checkpoint backend — 12/11/2026

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

O relatório completo ficou no chat; resumo para retomada (T1, T2 e T2.1 concluídas e integradas; T3 em revisão na branch):

- T3 — F-02: implementada na branch `codex/stale-delivery-recovery`, aguardando revisão e merge.
- T4 — F-03: campos de política de `config.example.yaml` (`blacklist`, `blocked_sellers`,
  `maximum_price`, limites por hora, `templates` etc.) são validados, mas nenhum código os lê.
- T5 — F-06 e F-07 (exigem investigação): máquinas de estado do domínio sem uso em produção;
  `listen` migra o banco runtime sem backup e o README não descreve isso.
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
