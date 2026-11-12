# Checkpoint backend — 12/11/2026

## Objetivo e estado

Corrigir a T2 da auditoria técnica de 12/11/2026, exclusivamente F-04 e F-05b: validação estrita
das URLs que entram no bot. F-04: os validadores do link afiliado devolvido pela API oficial
aceitavam quebra de linha, espaços, caracteres de controle, bidi e texto adicional, porque só
inspecionavam o resultado de `urlsplit`, que descarta tabs e quebras de linha em silêncio. F-05b:
`SafeUrlExpander` validava a URL inicial e cada `Location` só depois de `urlsplit`/`urljoin`.
Gates, contratos de entrega, deduplicação, estados duráveis e os demais achados não foram alterados.

- Repositório: `Lipe-meira/promo_bot`.
- Branch da tarefa: `codex/affiliate-url-validation-hardening` (reutilizar ao continuar esta tarefa).
- Base: `origin/main` em `c628d632be90602913ce1859f89be723efa19e82` (merge da T1).
- Último SHA verificado antes deste registro (implementação):
  `406d01c76439607f55d2d8cc594a0fd5d241aeaf`; o commit de F-04 é
  `182f5dd7eb1a0a9d5ce103f936fcf11481d94998`.
- Diff: [main...codex/affiliate-url-validation-hardening](https://github.com/Lipe-meira/promo_bot/compare/main...codex/affiliate-url-validation-hardening).
- Publicação: confirmar com `git ls-remote origin refs/heads/codex/affiliate-url-validation-hardening`.
  Este registro é um commit posterior ao da implementação; consulte seu SHA pelo Git.
- Sem autorização de merge para a T2. Nenhuma autorização para deploy, migrations reais,
  operações live ou envios pelo bot.

## Alterações e decisões

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

## Validações

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

O relatório completo ficou no chat; resumo para retomada (T1 e T2 concluídas):

- T3 — F-02: `DeliveryRepository.mark_stale_sending_ambiguous` não é chamado por código de produção.
- T4 — F-03: campos de política de `config.example.yaml` (`blacklist`, `blocked_sellers`,
  `maximum_price`, limites por hora, `templates` etc.) são validados, mas nenhum código os lê.
- T5 — F-06 e F-07 (exigem investigação): máquinas de estado do domínio sem uso em produção;
  `listen` migra o banco runtime sem backup e o README não descreve isso.
- T6 — F-08, F-09 e F-10: retry do `send-test --live`, `except: pass` silencioso em
  `coin_shadow_generation`, `doctor` com `"phase": 2` fixo, ausência de CI e documentação histórica.
- F-05a (transporte com IP fixo no expansor genérico) é opcional e maior.

## Riscos residuais da T2

- Provas já persistidas não são revalidadas no momento do envio: `shadow_delivery` e
  `AliExpressEnrichmentService._validate_proof` seguem checando só esquema e host.
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
