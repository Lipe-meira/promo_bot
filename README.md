# Promo Affiliate Bot

Bot local para monitorar mensagens autorizadas, converter links afiliados e garimpar produtos pela
API oficial do AliExpress. Usa Python 3.12, execução assíncrona e SQLite, com foco em Windows,
validação conservadora e fluxos shadow separados da publicação em produção.

## Estado atual

| Área | Implementado | Limite principal |
| --- | --- | --- |
| Relay Telegram | Leitura de canais autorizados, extração de links, persistência, fila e deduplicação | Ler uma origem não autoriza publicar seu conteúdo |
| AliExpress canônico | Geração oficial, correlação por produto e comparação exata do tracking retornado | Preview e entrega exigem prova atual e histórico durável |
| AliExpress coin-shadow | Conversão direta dos dois formatos estritos de short abaixo | Uma URL visível, sem expansão ou fallback canônico |
| Piloto privado | Listener limitado para canônicos e shorts, com destino `private-test` | Somente mensagens novas, gates exclusivos e orçamento compartilhado |
| Discovery shadow | Busca manual por `product.query` ou, opcionalmente, `hotproduct.query` | Uma operação por run, ranking privado, sem geração de links ou envio |
| Refinamento SKU | Requisitos estruturados, seleção conservadora e histórico por `(product_id, sku_id)` | Fluxo separado, bloqueado até confirmação da permissão SKU Dimension API |
| Histórico durável | Gerações, usos, tentativas de envio e consulta local sanitizada | Não autoriza reutilização nem comprova comissão |

Os caminhos reais AliExpress existem, mas começam com gates fechados. Os testes funcionais já
observados não garantem suporte universal, preço, desconto ou preservação de moedas para qualquer
conta, produto e momento.

Shopee continua no gate `SHOPEE_OFFICIAL_CONTRACT_UNAVAILABLE`. Mercado Livre tem um marco offline
de contratos, revisão manual e adapter de navegador falso; seus caminhos reais continuam
bloqueados. Reconhecer URLs de outras lojas não significa implementar suas APIs de afiliação.
Cupons, agendamento do garimpo e publicação automática não fazem parte dos fluxos shadow descritos
aqui.

## Garantias e limites

- Canônicos exigem tracking textual exatamente igual ao configurado, correlação por produto e a
  versão de contrato `top-link-generate-tracking-v2`. Provas antigas não ganham essa garantia
  retroativamente.
- Shorts exigem tracking exato e correlação dedicada: `SOURCE_VALUE_EXACT` ou
  `POSITIONAL_SINGLETON`, somente com uma entrada e um resultado sob as validações previstas.
- `tracking_confirmed` confirma o valor devolvido pela API, não comissão atribuída. Geração,
  clique ou envio não comprovam atribuição financeira; isso depende de evidência específica do
  Portals. `attribution_unverified=true` permanece no coin-shadow e no histórico.
- O contexto herdado de um short, como `utm` ou `from`, permanece opaco. O bot não o reconstrói e
  não comprova a precedência entre atribuições.
- Preços e descontos copiados de mensagens são informações da origem, não validações do bot.
  No discovery, preço product-level não é preço de uma variação. Desconto declarado pela API não
  equivale a queda contra o histórico observado.
- Histórico product-level mantém a operação de origem; histórico SKU permanece separado. Cache
  hit não cria uma nova observação live de preço nem uma nova geração afiliada.
- Reservas persistentes bloqueiam reenvio após resultado incerto. SQLite e Telegram não oferecem
  uma transação conjunta; o projeto não promete envio exactly-once.

## Instalação no Windows

Na pasta do checkout que deseja usar, instale o [uv](https://docs.astral.sh/uv/) e as dependências:

```powershell
winget install --id astral-sh.uv --exact
uv python install 3.12
uv sync --locked
```

Crie os arquivos locais somente se ainda não existirem; não sobrescreva sua configuração:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
if (-not (Test-Path -LiteralPath config.yaml)) {
    Copy-Item -LiteralPath config.example.yaml -Destination config.yaml
}
```

`.env`, `config.yaml` e `.private/` são locais e ignorados pelo Git. Não versionar credenciais,
sessões, bancos, respostas live ou URLs afiliadas reais. Banco runtime e sessão Telethon ficam,
por padrão, em `%USERPROFILE%\.promo_bot`; `PROMO_BOT_RUNTIME_DIR` permite outro diretório externo.
Os bancos shadow são selecionados explicitamente e não consultam o runtime implicitamente.

Mantenha estes controles e os gates live desativados até uma execução específica autorizada:

```env
DRY_RUN=true
PUBLISH_REAL_DEALS=false
SEARCH_ENABLED=false
PUBLISH_WITHOUT_AFFILIATE=false
COUPON_BROWSER_VERIFICATION=false
ALIEXPRESS_LIVE_API_ENABLED=false
ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED=false
ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED=false
ALIEXPRESS_DISCOVERY_SHADOW_ENABLED=false
ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_ENABLED=false
ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED=false
ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED=false
```

`DRY_RUN=true` impede publicação de produção, mas não impede uma entrega privada explicitamente
habilitada pelos gates shadow. `uv --offline` controla downloads de dependências, não chamadas
AliExpress ou Telegram feitas por um comando autorizado.

## CLI e piloto privado

Para conferir a configuração local e descobrir os argumentos disponíveis:

```powershell
uv run promo-bot validate-config
uv run promo-bot doctor
uv run --offline promo-bot aliexpress --help
uv run --offline promo-bot affiliate link-history --help
```

`init-db` altera o schema do banco selecionado e deve ser uma operação explícita. `listen` acessa
Telegram; `listen --authorize` solicita telefone, código e eventual 2FA interativamente. Não
inclua esses dados em argumentos, logs ou conversas. `send-test` mostra uma mensagem sintética;
`send-test --live` é um envio externo distinto. Esses comandos não substituem o piloto shadow.

Os comandos AliExpress são separados por finalidade:

- `convert-preview`: conversão canônica, com `--database` explícito para geração real.
- `shadow-preview` e `coin-shadow-preview`: preview de mensagem autorizada em banco shadow.
- `coin-shadow-auto-deliver`: entrega pontual ao destino privado autorizado.
- `shadow-auto-deliver --include-coin-shorts`: piloto com os dois caminhos e limites comuns.

O coin-shadow aceita literalmente apenas os formatos HTTPS abaixo, com token ASCII de 7 ou 8
caracteres, sem query, fragmento, userinfo, porta ou barra final:

```text
https://s.click.aliexpress.com/e/_[A-Za-z0-9]{7,8}
https://a.aliexpress.com/_[A-Za-z0-9]{7,8}
```

Por padrão, o short precisa ser a única URL visível da mensagem. Ele segue como único `source_value`, sem
GET, resolução, redirect, navegador ou reconstrução de destino. Somente o span do link é trocado;
o restante do texto é preservado. O link retornado não é aberto pelo bot.

O piloto exige um canal-fonte numérico autorizado por execução, destino privado `private-test`
da allowlist e banco shadow dedicado, durável e externo. Recebe somente mensagens novas após
ficar pronto, sem catch-up. Limites obrigatórios: `--max-messages`, `--run-seconds`,
`--max-api-calls`, `--max-links-per-message 1` e `--max-send-messages`. Canônicos e shorts compartilham
os contadores; uma segunda instância atualizada no mesmo banco falha no lock antes dos transportes.
Encerre listeners antigos antes do teste: bancos diferentes, versões antigas e outras máquinas
não têm deduplicação cruzada garantida.

O listener pode admitir até três ocorrências com o opt-in adicional
`--allow-multiple-coin-shorts --max-links-per-message 3`. Duas ou mais URLs devem ser
exclusivamente shorts estritos; cada literal distinto usa uma chamada singleton ou cache
válido. Apenas a mensagem completa validada produz preview e um único envio privado.
O canônico isolado continua funcionando, sem habilitar lote canônico. Consulte o
[roteiro limitado de múltiplos shorts](docs/ALIEXPRESS_MULTI_COIN_LISTENER_PILOT.md),
que inclui migration explícita de banco novo e desligamento de gates; não execute sem
autorização separada. Aumentar somente o limite não habilita essa extensão.

Geração real exige schema atualizado antes do listener. Não execute exemplos antigos sem conferir
checkout, argumentos, banco e pré-checagens atuais. A correção documental do roteiro anterior do
piloto permanece uma pendência separada; esta atualização não habilita nem executa o piloto.

## Garimpo e consulta de produtos

`aliexpress discovery-scan` usa perfis YAML configuráveis em BR/BRL/PT. O exemplo fica em
[docs/examples/aliexpress-discovery-profiles.example.yaml](docs/examples/aliexpress-discovery-profiles.example.yaml).
O padrão é `--source product-query`; `--source hotproduct` exige gate adicional e tetos iniciais de
5 itens por página, uma página por palavra-chave, duas chamadas e dez produtos por run. Não há
mescla automática entre operações. A Advanced API consta como Active na evidência registrada;
isso não habilita o gate hot automaticamente.

Cache de busca: 60 minutos. Claims concorrentes encerram imediatamente, sem polling. O ranking
usa somente histórico live da mesma operação e produto, com dois runs anteriores distintos na
janela de 30 dias. Primeira coleta é baseline; `PROVIDER_DISCOUNT_ONLY` não comprova queda histórica.

Para consultar uma varredura, use exatamente o ID e o banco daquela execução:

```powershell
$runId = [int](Read-Host 'run_id da varredura que deseja consultar')
$shadowDatabase = Read-Host 'Caminho absoluto do banco usado nessa varredura'

uv run --offline promo-bot aliexpress discovery-results `
    --run-id $runId --shadow-database $shadowDatabase

uv run --offline promo-bot aliexpress discovery-results `
    --run-id $runId --shadow-database $shadowDatabase --format links
```

O JSON padrão mostra metadados. `--include-products` acrescenta produtos ao JSON; `--format links`
lista título, preço BRL ou indisponível, operação de origem, URL canônica e aviso product-level.
Essa URL é derivada localmente do ID validado, sem tracking ou URL da API, e não é aberta.
Essas consultas são read-only, sem TOP, Telegram ou refinamento SKU.

JSON usa escape ASCII para não falhar em stdout CP1252. A lista textual escapa apenas caracteres
não representáveis; para mostrar Unicode integralmente no PowerShell, configure
`$env:PYTHONIOENCODING = 'utf-8'` antes do comando.

`discovery-sku-refine` e `discovery-sku-results` são outro fluxo manual. A SKU Dimension API consta
como Pending na evidência registrada: não habilite o refinamento antes de confirmação explícita.
Um match precisa de propriedades estruturadas inequívocas e `sale_price_with_tax` positivo em BRL;
títulos não selecionam SKU, ambiguidades exigem revisão e estoque não é comprovado. Seu cache e
histórico não se misturam ao preço product-level.

## Histórico durável de links

O histórico AliExpress registra fatos separados em `affiliate_link_generations`,
`affiliate_link_uses` e `affiliate_link_use_links`. Uma geração pode ter vários usos; um envio
pode conter vários links. `CONFIRMED` exige resposta validada e persistida. Cache referencia a
geração original; não inventa nova chamada ou data de geração.

Geração real exige SQLite absoluto, local, durável, fora de diretórios temporários e com schema
atualizado. A migration `9b3d5e7f1a20` é aditiva, sem importação retroativa. Escolha explicitamente
o banco antes de migrar; nenhum comando de geração migra seu banco automaticamente.

`CALL_STARTED` é persistido antes da rede; reservas e registros de uso precedem o envio. Falha de
persistência antes do envio implica zero Telegram. Tentativas incertas permanecem bloqueadas,
inclusive após reinício. Histórico sobrevive ao purge operacional, sem purge ou backup automático
nesta entrega; downgrade recusa tabelas históricas preenchidas.

Consultas não carregam `.env`, não migram, não recuperam leases e não fazem rede. Escolha o mesmo
banco e escopo do fluxo que deseja auditar:

```powershell
$historyDb = Read-Host 'Caminho absoluto do banco shadow com schema de histórico atualizado'

uv run --offline promo-bot affiliate link-history list `
    --database $historyDb --scope shadow --platform aliexpress `
    --generation-result CONFIRMED --limit 20

uv run --offline promo-bot affiliate link-history list `
    --database $historyDb --scope shadow --generation-result CONFIRMED `
    --send-result SEND_CONFIRMED

$generationId = Read-Host 'UUID da geração obtido na consulta'
uv run --offline promo-bot affiliate link-history show `
    --database $historyDb --scope shadow --generation-id $generationId
```

Para exibir o link completo sensível, acrescente explicitamente `--include-urls` a `list` ou `show`.
Sem essa opção, URLs ficam ocultos. Períodos de geração e uso e resultados de geração e envio têm
filtros independentes. Consultar um link expirado não autoriza reutilizá-lo nem dispensa validação.

Legado sem vínculo durável comprovado bloqueia preview/envio, mesmo expirado. `legacy-blocks`
permite inspecionar os bloqueios; `request-legacy-generation` registra uma solicitação explícita
para uma identidade, com snapshot `LEGACY_NOT_REVALIDATED`, sem TOP ou confirmação retroativa.
Só previews manuais podem consumir essa solicitação. Ela nunca desbloqueia `UNCERTAIN`; quando o
contexto de chave não permite provar a correspondência, o bloqueio permanece sem recuperação
operacional nesta entrega. Consulte o guia antes de solicitar essa transição.

## Qualidade offline

```powershell
uv lock --check --offline
uv run --offline ruff format --check .
uv run --offline ruff check .
uv run --offline mypy src
uv run --offline pytest -m "not live and not browser"
git diff --check
```

O pytest também exclui `live` e `browser` por padrão, desabilita leitura do `.env` real e gates
externos nos testes offline e usa transportes falsos. Sockets externos são bloqueados; loopback
é permitido para o mecanismo interno do event loop do Windows. Testes de migration usam somente
bancos temporários, nunca bancos do operador. Não execute testes live apenas para validar código
ou documentação.

## Guias detalhados

- [Arquitetura](docs/ARCHITECTURE.md) e [especificação do produto](docs/PRODUCT_SPEC.md).
- [Relay Telegram](docs/TELEGRAM_RELAY.md) e [entrega shadow](docs/SHADOW_DELIVERY.md).
- [Contratos e conversão AliExpress](docs/ALIEXPRESS_AFFILIATE.md).
- [Shorts de moedas e listener privado](docs/ALIEXPRESS_COIN_SHADOW.md).
- [Discovery, hotproduct e refinamento SKU](docs/ALIEXPRESS_DISCOVERY_SHADOW.md).
- [Consulta, recuperação e transição legada do histórico](docs/AFFILIATE_LINK_HISTORY.md).
- [Relatório de implementação do histórico](docs/DURABLE_LINK_HISTORY_IMPLEMENTATION.md).
- [Shopee](docs/SHOPEE_AFFILIATE.md) e [Mercado Livre](docs/MERCADO_LIVRE_AFFILIATE.md).
