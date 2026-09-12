# Entrega shadow manual e única

`promo-bot affiliate shadow-deliver` entrega um preview existente, sem conversão, renovação da
prova ou chamadas ao provider. Serviço, repository, modelo, configuração e transporte são genéricos.
A implementação foi validada somente offline; nenhum envio real faz parte desta etapa.

O caminho automático limitado é documentado separadamente no fim deste arquivo. O gate manual não
autoriza o automático, e o gate automático exige que o manual esteja desligado.

## Exceção explícita, não publicação de produção

São obrigatórios, simultaneamente:

```text
TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED=true
DRY_RUN=true
PUBLISH_REAL_DEALS=false
PUBLISH_WITHOUT_AFFILIATE=false
SEARCH_ENABLED=false
```

O gate novo é `false` por padrão. Somente o comando manual com
`--confirm-send-one-test-message` pode usar a exceção para enviar uma mensagem real a um canal
privado de teste. `DRY_RUN=true` continua bloqueando a publicação de produção, mas **não significa
ausência de efeito externo neste comando explicitamente autorizado**. Sem a confirmação, falha
fechado. Listener e preview não passam a publicar por causa desse gate.

O sucesso retorna JSON sanitizado com `status=sent`, `external_side_effect=true`,
`get_chat_attempts=1`, `send_message_attempts=1`, `production_publication=false`, alias e IDs internos.
Não retorna chat ID, token, texto, link, resposta bruta ou ID externo da mensagem. Nos erros,
`external_side_effect=true` significa que a tentativa de envio começou: não comprova recebimento.

## Allowlist e credencial

Adicione manualmente ao YAML usado com `--config` (por padrão `config.yaml`), substituindo o ID
**fictício** pelo do canal privado exclusivo de teste:

```yaml
telegram_shadow_delivery:
  allowed_destinations:
    private-test:
      chat_id: "-1001111111111"
      kind: private_channel
```

O destino é selecionado somente por `--destination private-test`; não há argumento de chat ID.
Aliases usam letras minúsculas, números e hífen, começam por letra e têm até 40 caracteres.
Aliases e IDs duplicados são recusados. O loader agora rejeita chaves YAML duplicadas, em vez de
substituir silenciosamente um valor anterior; isso também se aplica aos demais mapas do arquivo.

`source_channels` continua sendo a lista YAML de origens, separada dessa allowlist. **Nesta fase da
entrega todas as origens devem estar na forma numérica `-100...`**, inclusive canais públicos.
Uma origem configurada por username causa `SHADOW_SOURCE_NUMERIC_ID_REQUIRED`: o comando não usa
Telethon para resolver identidades. Isso permite provar localmente que o destino difere de todas
as origens; também se compara o canal registrado na mensagem que originou o preview.

O `getChat` confirma ID exato, tipo `channel`, ausência de `username` e de `active_usernames`.
Um canal público, grupo, ID divergente ou falha de validação é recusado antes do envio.
O operador deve assegurar que é realmente um canal de teste; privacidade não comprova essa finalidade.

A única credencial utilizada **pela entrega** é `TELEGRAM_BOT_TOKEN`. O bot deve ser administrador
somente do canal privado de teste, com permissão para publicar mensagens (`post_messages`) e sem
permissões administrativas adicionais desnecessárias. Não são usados `TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`, sessão Telethon, `TELEGRAM_TARGET_CHAT_ID`, credenciais AliExpress ou tracking ID.
O comando não exige os gates de API live nem os ativa.

## Conteúdo, validade e transporte

Antes da rede, exige preview existente `READY`, conteúdo presente/não purgado e ainda dentro de
`content_expires_at`; prova `CONFIRMED`, validada e não expirada; provider/store, link, produto,
candidato e mensagem de origem correlacionados. A validade e o texto são conferidos novamente
depois do `getChat`. Não há reconstrução, renovação ou transformação do conteúdo.

Envia exatamente `preview.rendered_text`, com `parse_mode=None`, sem botão, entidades, enriquecimento
ou divisão. A geração automática de preview visual do link é desativada. O preflight usa um limite
conservador de 4096 unidades UTF-16; conteúdo maior causa `SHADOW_MESSAGE_TOO_LONG` antes da rede.
Alguns textos com muitos caracteres suplementares podem ser recusados conservadoramente.

A camada reutilizada é a biblioteca Bot API existente (`python-telegram-bot`), não o publicador de
deals. O ciclo de vida inicializa apenas o request HTTP, evitando o `getMe` implícito de
`Bot.initialize`. A sequência permitida é no máximo um `getChat` e um `sendMessage`, sem retry,
com TLS normal, `trust_env=False`, redirects desativados e limite de 15 segundos por operação.
Não há superfície de edição, encaminhamento, clique ou leitura de mensagens neste transporte.
Logs de payload HTTP, Telegram e SQL ficam silenciados durante a operação, inclusive em escopos
concorrentes. O stdout contém somente o relatório sanitizado, nunca o conteúdo enviado.

## Persistência e incerteza

A migration **`f7a29b6c103e`**, posterior a `e4c19a7b52d0`, cria `affiliate_shadow_deliveries` com
FK para preview, chave de destino, estado, contador limitado a 1, início/fim, ID Telegram,
código sanitizado, timestamps e `UNIQUE(preview_id, destination_key)`.
Por pertencer à cadeia Alembic compartilhada, a tabela estrutural poderá existir também no schema
principal após upgrades. Isso não autoriza uso: o serviço exige `AffiliateShadowDatabase` e o
repository exige a marca de sessão shadow. Não cria `Deal`, `Delivery` ou outbox.

O caminho padrão é `PROMO_BOT_RUNTIME_DIR/shadow/aliexpress-shadow.sqlite3`; sem essa variável,
usa o runtime externo padrão `.promo_bot` do usuário. O nome legado do arquivo foi preservado para
reencontrar os previews existentes. `--shadow-database CAMINHO.sqlite3` permite seleção explícita,
sempre fora do repositório e diferente do banco principal. Nenhuma conexão com o banco principal
é aberta. A entrega requer um SQLite já existente e migrado; não executa migrations durante envio.
Os comandos existentes de criação/consulta de preview atualizam somente o SQLite shadow.

A reserva é atômica e confirmada antes do preflight. `destination_key` é um hash do ID canônico do
destino, não do alias; renomear o alias não libera outra tentativa. Não é um mecanismo de criptografia
do ID; o hash não é exibido. A transição `pending → sending`, com `attempt_count=1`, é confirmada no
SQLite antes de chamar `sendMessage`.

- Confirmação válida com ID de mensagem: `sent`.
- Recusa comprovada/preflight: `failed_safe`.
- Timeout, desconexão ou resposta ambígua durante envio: `uncertain`.
- Telegram confirmou, mas gravação final falhou: registro permanece `sending`; relatório incerto.
- Em verificações posteriores, `sending` tem significado efetivo `uncertain`.

**Todo registro existente bloqueia a repetição**, inclusive `pending`, `failed_safe`, `sent` e
`uncertain`. Não há retry automático nem comando para liberar tentativa. Falha de limpeza após
resultado conhecido preserva os contadores/estado e retorna `SHADOW_CLEANUP_FAILED`, sem reenviar.
Uma interrupção externa é propagada após tentativa de registrar seu resultado; não se presume que
um envio interrompido falhou antes de chegar ao Telegram.

A proteção vale para o mesmo banco persistente: não apague, restaure uma cópia antiga ou troque o
SQLite para tentar novamente. Não há transação distribuída SQLite/Telegram nem garantia de
exactly-once diante de perda do arquivo. Uma nova tentativa exige preview novo **e nova autorização
manual**, não alteração do estado anterior. A criação da tabela não copia texto/link para entregas;
a retenção/purge de conteúdo dos previews permanece em 24 horas.

## Preparação futura de um preview novo — não executar sem autorização

Não reutilize automaticamente o exemplo histórico `preview_id=2`. Para criar um preview novo,
use o listener **já existente**, em etapa separada: ele lê uma mensagem nova autorizada, podendo
gerar link real se não houver cache. Essa etapa requer sua própria autorização, credenciais
Telegram/AliExpress e sessão já autorizada; não é executada pela entrega.

```powershell
$env:DRY_RUN = "true"
$env:PUBLISH_REAL_DEALS = "false"
$env:PUBLISH_WITHOUT_AFFILIATE = "false"
$env:SEARCH_ENABLED = "false"
$env:COUPON_BROWSER_VERIFICATION = "false"
$env:TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED = "false"
$env:ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED = "true"
$env:ALIEXPRESS_LIVE_API_ENABLED = "true"
try {
    uv run --env-file .env promo-bot aliexpress shadow-listen --max-messages 1 --run-seconds 60 --max-api-calls 1
} finally {
    $env:ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED = "false"
    $env:ALIEXPRESS_LIVE_API_ENABLED = "false"
}
```

Selecione conscientemente o ID novo, `READY` e válido no **mesmo** SQLite. As consultas abaixo
não fazem chamada Telegram/AliExpress; `list` também aplica migrations shadow e purge de expirados:

```powershell
uv run --env-file .env promo-bot aliexpress shadow-previews list --limit 20
$previewId = Read-Host "ID interno do preview novo aprovado"
uv run --env-file .env promo-bot aliexpress shadow-previews show --preview-id $previewId --include-content
```

Somente esse `show --include-content` expõe conteúdo completo no stdout para inspeção explícita.
Não copie essa saída para logs gerais. Caso use `--shadow-database`, repita o mesmo caminho em todos
os comandos. Verifique também a prova: a entrega fará sua validação obrigatória antes da rede.

## Futura entrega única — autorização separada ainda necessária

Após revisar o texto e receber autorização específica para o ID novo/destino:

```powershell
$env:DRY_RUN = "true"
$env:PUBLISH_REAL_DEALS = "false"
$env:PUBLISH_WITHOUT_AFFILIATE = "false"
$env:SEARCH_ENABLED = "false"
$env:ALIEXPRESS_LIVE_API_ENABLED = "false"
$env:ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED = "false"
$env:TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED = "true"
try {
    uv run --env-file .env promo-bot affiliate shadow-deliver --preview-id $previewId --destination private-test --config config.yaml --confirm-send-one-test-message
} finally {
    $env:TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED = "false"
}
```

Este comando produz **um envio real**, não uma simulação. O gate é novamente fechado no processo ao
terminar. A implementação e os testes não executam esses exemplos nem leem arquivos locais protegidos.

## Validação offline

Testes usam SQLite temporário, cliente falso e `httpx.MockTransport` na biblioteca Bot API real.
Cobrem wire literal sem botão/entities, uma chamada por método, concorrência, todos os estados
terminais, ambiguidade, falha de persistência, expiração, gates, allowlist, correlação genérica,
CLI pelo `entrypoint` real e logs em DEBUG. A suíte desabilita leitura implícita do `.env` e sockets
externos; testes live/browser ficam excluídos. A migration tem round-trip, constraints, head único
e comparação Alembic com o metadata. Nenhum serviço live é necessário.

## Validação real sanitizada da entrega manual

Em 2026-09-11, o operador executou localmente uma entrega shadow manual para um destino privado
presente na allowlist. O resultado confirmou `status=sent`, `persisted_state=sent`, uma tentativa
de `getChat`, uma tentativa de `sendMessage`, `external_side_effect=true` e
`production_publication=false`. Exatamente uma mensagem foi enviada ao canal privado autorizado.

A validação comprova o gate exclusivo, a confirmação manual, a validação do destino, a transição
durável para `sending`, o envio literal único e a persistência terminal em `sent`. Nenhuma
publicação de produção foi realizada. Este registro não contém IDs internos, chat IDs, texto,
links, credenciais, dados de sessão ou resposta bruta do Telegram, e não autoriza novas entregas.

## Entrega automática shadow limitada

O comando `promo-bot aliexpress shadow-auto-deliver` reúne recepção de uma mensagem nova,
conversão AliExpress, persistência do preview e uma única tentativa de entrega ao alias
`private-test`. Ele não reutiliza o comando manual nem sua confirmação. Seu gate exclusivo é:

```text
ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED=true
```

Para evitar combinação acidental de capacidades, `ALIEXPRESS_TELEGRAM_SHADOW_ENABLED`,
`ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED` e `TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED` precisam
estar `false`. Os gates de API e segurança permanecem independentes e obrigatórios: API live ligada,
`DRY_RUN=true`, todas as flags de publicação/busca desligadas e verificação por browser desligada.

A entrega automática aceita um token de autorização estrutural criado somente após validar os
gates, exatamente uma origem numérica, destino privado diferente da origem e alias `private-test`.
O serviço continua validando `getChat`, validade do preview e todas as correlações multi-link. Para
previews novos, cada linha de `affiliate_shadow_preview_links` precisa ligar a mensagem-fonte, o
candidato e uma prova oficial válida; soma de ocorrências, primeira prova e links presentes no texto
precisam coincidir. Qualquer adulteração falha antes de `sendMessage`.

A idempotência direta usa `UNIQUE(source_message_id, destination_key)`. Assim, recriar um preview da
mesma mensagem não libera novo envio. `sending` ou qualquer estado terminal impede retry automático;
uma resposta ambígua continua `uncertain`. O contador `max-send-messages` é incrementado somente no
limite imediato do despacho externo. Atingir um limite encerra novas admissões, sem cancelar o item
já aceito.

Comando da primeira execução futura, a realizar apenas mediante autorização específica:

```powershell
uv run --env-file .env promo-bot aliexpress shadow-auto-deliver `
  --destination private-test `
  --max-messages 1 `
  --run-seconds 60 `
  --max-api-calls 1 `
  --max-links-per-message 3 `
  --max-send-messages 1
```

O comando usa somente o SQLite shadow externo, não abre o banco principal e não cria `deals`,
deliveries de produção ou outbox. Os testes usam evento Telegram falso, `MockTransport`, Bot API
falsa e bancos temporários. Nenhum listener, request AliExpress ou envio Telegram real foi executado
durante a implementação.
