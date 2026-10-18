# Intermediários seuhardware no clonador privado

Extensão manual, desligada por padrão, sobre a main `0efca605`. Não habilita
publicação, outros destinos, monitor contínuo nem o resolvedor canônico.
Uma observação HTTP fornecida pelo operador motivou o contrato; não houve nova
sondagem do domínio nesta implementação. Ela não garante seu comportamento futuro.

## Habilitação e contrato

Somente `aliexpress shadow-auto-deliver --include-coin-shorts` oferece o opt-in
adicional `--allow-seuhardware-wrappers`. Os gates existentes, uma fonte numérica
autorizada, banco shadow durável explícito, lock e destino `private-test` continuam
obrigatórios. Sem a nova flag, o comportamento anterior é preservado. Não há
alteração da allowlist global nem suporte nos comandos manuais de conversão.

Formato integral: `https://seuhardware.com/s/[A-Za-z0-9]{7}`. Exemplo **sintético**:
`https://seuhardware.com/s/Ab12Cd3`. HTTPS/host/case/path exatos; sem query, fragmento,
porta explícita, userinfo, codificação alternativa ou barra final.

O padrão continua sendo uma ocorrência. Para duas ou três, também são necessários
`--allow-multiple-coin-shorts --max-links-per-message 3`. Ocorrências repetidas contam
para esse limite. São admitidas misturas de intermediários e shorts AliExpress
diretos. Canônico único continua funcionando; mistura com canônico ou qualquer
outro link, superfície oculta, entidade URL/span UTF-16 inválido ou mais de três
ocorrências é rejeitada **antes de qualquer resolução HTTP ou TOP**.

## Resolução limitada e proteção de rede

1. Conferir toda a mensagem e disponibilidade de vaga de envio.
2. Para cada intermediário literal distinto, fazer no máximo um GET, sem retry.
3. Resolver DNS dentro do prazo total; rejeitar qualquer IP não público ou inválido.
4. Selecionar um único IP público e usar o transporte IP-pinned existente, com TLS/SNI
   e Host originais. O peer precisa corresponder ao IP selecionado. Sem proxy de
   ambiente, cookies anteriores, credenciais HTTP ou fallback para outro IP.
5. Aceitar somente HTTP **302**, com `Location` absoluto que passe, literalmente,
   pelo validador coin existente de `a.aliexpress.com` ou `s.click.aliexpress.com`.
6. Não seguir esse destino, outro intermediário, ciclo ou cadeia. Não interpretar
   HTML/JavaScript e não abrir navegador. Não expandir o short AliExpress.

Prazo total por resolução: **8 segundos**, incluindo DNS; timeout HTTP: **5 segundos**.
Headers aceitos: no máximo **16 KiB**. O requester usa streaming e não lê corpo de
resposta na aplicação; o stack HTTP pode bufferizar bytes ao receber os headers,
antes da checagem local. O parser HTTP também conserva seus próprios limites.
Timeout, HTTP inesperado, Location ausente/inválido, peer divergente e demais
falhas produzem somente códigos `SEUHARDWARE_*` sanitizados, sem URL ou texto externo.

Todas as resoluções devem terminar antes da primeira geração TOP. O intermediário
**nunca** é `source_value`: somente o short AliExpress retornado e validado é
enviado, literalmente, em uma chamada singleton. Tracking textual exato, contrato,
claims, marcação durável anterior à rede, limites e bloqueios de `UNCERTAIN` não mudam.

## Cache, texto, histórico e contadores

O literal original e seus spans continuam associados à mensagem de origem. O mapa
em memória separa original, short resolvido e short enviado à TOP. Repetições do
mesmo intermediário fazem um GET por mensagem; intermediários diferentes resolvendo
para o mesmo short, ou esse short direto na mensagem, compartilham uma geração.

O cache continua indexado pelo short AliExpress e tracking/contexto de chave, com o
TTL e validações existentes. **Não existe cache de intermediário → destino.** Uma
mensagem nova resolve novamente cada intermediário, mesmo quando a geração final
vem de cache; o destino externo pode mudar. Histórico não autoriza reutilização.

`wrapper_http_requests` conta vagas de despacho HTTP adicionais, separadas de
`api_calls` (TOP) e `send_messages`. Falha de conexão pode consumir uma vaga sem
comprovar recebimento do GET pelo servidor. Por mensagem, o máximo é o número de
intermediários literais distintos, limitado a três; por execução, no máximo
`3 × max_messages`, sem retries. O limite temporal do listener também se aplica.

`coin_multi` conserva ocorrências e passa a contar entradas distintas pelo short
**resolvido**. Exemplo: W→A, V→A, A direto são três ocorrências, dois GET e uma
entrada TOP distinta; havendo cache válido de A, nenhuma TOP é necessária. Cache
parcial/total e repetição local mantêm a semântica por geração, não por GET.

O saldo TOP é comparado aos misses dos shorts resolvidos antes de qualquer geração;
orçamento insuficiente não faz TOP. Os GET já realizados permanecem contabilizados.
Expiração/corrida posterior à inspeção pode deixar gerações confirmadas sem preview,
como no fluxo anterior: elas não são desfeitas nem repetidas automaticamente.

Um preview agregado completo preserva o texto fora dos spans originais, inclusive
Unicode, emojis, CRLF, rótulos, preços e cupom. O limite final continua 4096 unidades
UTF-16. Um único intermediário também utiliza esse preview agregado, sem mudar o
preview singleton anterior. A reserva compartilhada continua única por
mensagem/destino; o único SEND referencia todas as gerações necessárias. Qualquer
falha/resultado incerto impede entrega parcial, e envio incerto não libera retry.

Não há migration: usam-se as tabelas de `b8c2e4f6a901` e o JSON `origin` dos usos.
Preview agregado com uma ocorrência só é válido com auditoria de intermediário.
PREVIEW e SEND preservam `wrapper_resolution_version=1` e `wrapper_resolutions`:
ordinal, spans originais, HMAC contextualizado do original, fingerprint do short
resolvido/solicitado, UUID da geração, status 302 e instante observado. A validade
declarada é `MESSAGE_ONLY_NOT_CACHE`; é um fato de resolução daquela mensagem,
nunca uma autorização para reutilizar o mapeamento. Não há URL extra em texto bruto
nesses metadados. O literal já está no texto de origem; o vínculo durável identifica
o short por sua fingerprint de entrada da geração, sem reconstruí-lo do link gerado.
As validações conferem a associação com a geração e a cópia do mapa no SEND.
Purge conserva reservas e fatos duráveis; não amplia a retenção do conteúdo do post.

Para uma geração nova, as observações dos wrappers correspondentes também são
gravadas em seu `origin` já em `PREPARED`, antes da TOP. O UUID é o da própria linha;
o mapa do uso completo acrescenta esse UUID explicitamente. Assim, A confirmado
continua associado aos wrappers/spans originais mesmo se B falhar e nenhum PREVIEW
for criado. O instante observado é capturado após aceitar o 302, não ao montar o
preview. Uma geração reutilizada de cache **não** tem sua origem reescrita: a nova
observação pertence apenas aos novos usos da mensagem.

Falha de persistência impede envio. Falha ou timeout de resolução não cria geração
nem é uma tentativa TOP incerta; falha/timeout após TOP continua submetida ao
histórico e bloqueios existentes. Um novo evento não repete uma entrega concluída.

## Futuro piloto — não executado

Exige autorização separada. Reutilize o wrapper temporário UTF-8, preflight de
tracking/overrides, fonte, destino, concorrência e gates do
[roteiro multi-short](ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md). Selecione explicitamente
a worktree desta implementação e ajuste a verificação de branch para
`codex/seuhardware-short-wrapper`; não use o checkout operacional por suposição.
Crie/migre somente um banco novo, permanente e externo, se isso for autorizado.
Mantenha-o no `finally`, desligue os gates e remova somente o wrapper da tentativa.

Argumentos do listener, além dos caminhos absolutos de config/banco e destino:

```text
--include-coin-shorts --allow-multiple-coin-shorts --allow-seuhardware-wrappers
--max-links-per-message 3 --max-messages 5 --run-seconds 600
--max-api-calls 5 --max-send-messages 5
```

Aguardar `Telegram listener connected` e o segundo posterior à prontidão. Enviar
manualmente uma mensagem nova por vez, esperando entrega/diagnóstico anterior:

1. W válido sozinho: um GET, geração de A se sem cache, uma entrega.
2. W + A direto: GET novo de W; se continuar resolvendo para A, cache e uma entrega.
3. W + V + W: dois GET, até duas entradas resolvidas distintas, uma entrega completa.
4. W + canônico: rejeição antes de GET/TOP, sem entrega.
5. W com query: rejeição antes de GET/TOP, sem entrega.

Use entradas do operador, não os exemplos sintéticos, e não presuma que V aponta
para A/B nem que W conserva o destino. Confrontar contadores com gerações e usos
distintos do histórico; quatro links no SEND não representam quatro envios.
Interromper em falha/resultado incerto, sem nova tentativa automática ou aumento
dos limites. Esses números são expectativas condicionais, não resultados live.

Tracking confirmado não comprova comissão, precedência sobre contexto herdado,
cupom, preço final ou preservação de Moedas/APP/PC. Preços/descontos no texto são
informações da origem. A pendência documental do piloto anterior continua separada.

## Entrega e verificação offline — 18/10/2026

Branch: `codex/seuhardware-short-wrapper`, em worktree isolada criada pelo mecanismo
nativo. Base: `0efca60541787595f71d488e1eb4e08ed805aff9`, então igual à referência
remota de main. Nenhuma mudança posterior na base precisou ser incorporada.
O checkout operacional não foi trocado de branch nem teve arquivos modificados.

Evidência RED → GREEN:

- Entrada real: opt-in inexistente inicialmente recusado; depois, 302 sintético
  resolve para short validado, gera com tracking exato e entrega o texto completo.
- Orçamento SEND esgotado: o teste reproduziu GET desnecessário; agora recusa antes
  de HTTP/TOP. Orçamento TOP insuficiente recusa após resolver, antes de gerar.
- Query vazia: o extrator antigo tratava `?` como pontuação; a guarda específica
  recusa esse delimitador no intermediário antes de DNS, sem alterar shorts diretos.
- Revisão independente encontrou protocolo HTTP malformado não traduzido pelo
  transporte customizado. Requester real/stream falso e entrypoint reproduziram
  o erro; `httpcore.ProtocolError` agora vira código sanitizado de resolução,
  sem TOP/retry ou falsa tentativa TOP incerta.
- A confirmado/B rejeitado ou incerto: regressões reproduziram perda da associação
  no histórico da geração. O vínculo agora é persistido com sua origem antes de
  TOP, inclusive quando nunca haverá PREVIEW. Cache não reescreve a origem antiga.

Resultados finais aplicáveis ao código entregue:

| Verificação | Resultado |
|---|---|
| Baseline focado anterior | 38 aprovados |
| Rodada focada ampliada | 144 aprovados, incluindo storage/delivery/migrations temporárias |
| Regressões novas na suíte final | 92: 23 entrypoint, 13 orquestração, 56 segurança |
| `uv lock --check --offline` | Aprovado, 41 pacotes; lockfile sem alteração |
| Ruff format/check | Aprovados, 237 arquivos Python |
| `mypy src` | Aprovado, 101 arquivos fonte |
| `pytest -m "not live and not browser"` | **1177 aprovados, 5 excluídos, 6 warnings; 529,11 segundos** |
| Migration/FKs/purge | Regressões existentes aprovadas em SQLite temporário; nenhuma migration nova |
| `git diff --check` | Aprovado |

Uma rodada intermediária foi interrompida antes de concluir para corrigir a
rastreabilidade parcial; não é contabilizada como validação aprovada. Uma nova
sonda de teste corrigiu somente sua expectativa de estado pré-rede: o cliente
offline existente marca `CALL_STARTED` conservadoramente antes do transporte;
a associação é conferida por conexão SQLite separada, antes do request falso.
A suíte completa final acima foi executada depois dessas correções.

Os seis warnings são preexistentes: quatro avisos do ciclo de FKs
`affiliate_candidates`/`affiliate_link_proofs`/`deals` na autogeração Alembic, que
não valida integralmente a ordenação daquele ciclo, e duas depreciações do adapter
datetime sqlite3/Python 3.12. Os testes de FKs e purge continuam com enforcement;
nenhum warning novo de resolução, recuperação ou envio foi identificado.

Configurações sintéticas, `.env` real desabilitado, credenciais removidas do
ambiente filho dos testes, gates externos falsos e sockets externos bloqueados.
HTTP, TOP e Telegram foram falsos; nenhum domínio foi sondado, link real gerado,
banco real migrado, sessão acessada ou mensagem enviada. A revisão independente
não avaliou comportamento live nem condições comerciais, explicitamente fora do
escopo. Os dois P2 encontrados foram corrigidos com regressões; não houve achado
menor adiado desta mudança.

Arquivos da implementação:

- `src/promo_bot/security/seuhardware_short_links.py`;
- `src/promo_bot/affiliate/coin_shadow_multi.py` e `aliexpress_shadow_listener.py`;
- `src/promo_bot/cli.py` e `src/promo_bot/telegram/monitor.py`;
- `src/promo_bot/database/coin_shadow_multi_repository.py`,
  `coin_shadow_repository.py` e `history_repository.py`;
- `tests/unit/test_seuhardware_listener.py`, `test_seuhardware_orchestration.py`
  e `test_seuhardware_security.py`;
- este documento, `docs/ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md` e `README.md`.

Decisões de execução: reutilizar o schema e JSON existentes (sem migration),
preview agregado unitário somente com auditoria válida, HMAC em domínio exclusivo,
observações por mensagem/geração sem cache do intermediário, IP único sem failover.
Custos: diagnóstico fechado se esse IP falhar e nenhuma garantia do comportamento
real do domínio. O bookkeeping dos ciclos usou registro ignorado local em PowerShell
em vez dos helpers bash das skills; não alterou `.gitignore` ou arquivos do operador.
O piloto da nova opção continua pendente de autorização separada, sem push/PR/merge
nesta entrega local.
