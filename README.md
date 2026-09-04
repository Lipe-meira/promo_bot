# Promo Affiliate Bot

Bot local e seguro para monitorar alertas de promoções e preparar candidatos a ofertas. O projeto é
voltado ao Windows, Python 3.12, execução assíncrona e evolução controlada por fases.

## Estado atual

As Fases 1 e 2 estão implementadas. A Fase 3 — Shopee Brasil — está em desenvolvimento com o
cliente real bloqueado até a confirmação do contrato no Explorer oficial autenticado. A Fase 2
adiciona:

- monitoramento somente leitura dos canais declarados em `source_channels`, usando Telethon;
- catch-up recente e limitado antes do fluxo em tempo real;
- persistência antes da fila, recuperação após falhas e deduplicação após reinício;
- extração de links presentes em texto, entidades e botões;
- expansão fail-closed de encurtadores conhecidos;
- identificação e URL canônica de Amazon Brasil, Mercado Livre, Shopee, AliExpress e KaBuM!;
- estados explícitos `PENDING_AFFILIATE` e `MANUAL_REVIEW`;
- preview sintético e envio sintético opcional pela Telegram Bot API.

Ainda não existe provider de rede ativo, geração real de links afiliados, consulta real de produtos,
busca independente, Playwright, teste de cupons ou scheduler. Por isso, nenhum candidato real é
enviado ao Telegram.

A infraestrutura independente do contrato da Fase 3 está implementada: candidatos afiliados,
retomada, DTOs, política de preço e variação, evidência afiliada, formatter textual e outbox de
entrega. Tudo é validado com fixtures offline e isso não representa integração ativa com a Shopee.
SaAPI e outros wrappers não oficiais não são fonte do contrato.

O cliente real continua bloqueado por `SHOPEE_OFFICIAL_CONTRACT_UNAVAILABLE`. Nenhuma assinatura,
query, mutation ou regra de parsing foi presumida.

O primeiro marco offline da Fase 6 prepara o modo manual do Mercado Livre e separa entrega interna
para revisão, aprovação humana e divulgação externa. O acesso ao portal real, a geração real de
links, o worker contínuo, qualquer entrega real e o modo headless permanecem bloqueados. Consulte
[`docs/MERCADO_LIVRE_AFFILIATE.md`](docs/MERCADO_LIVRE_AFFILIATE.md).

A Fase 4 possui agora um marco offline do AliExpress Affiliate: canonicalização, seis contratos de
payload, DTOs, parsing dos dois envelopes documentados, evidência afiliada, persistência, preview e
preparação TOP assinada sem I/O. O transporte real permanece fechado por
`ALIEXPRESS_OFFICIAL_SIGNING_CONTRACT_UNAVAILABLE` até que o gateway Affiliate seja confirmado por
fonte oficial e o contrato operacional possa ser validado separadamente. Consulte
[`docs/ALIEXPRESS_AFFILIATE.md`](docs/ALIEXPRESS_AFFILIATE.md).

## Instalação no Windows

Instale o [uv](https://docs.astral.sh/uv/) e o Python isolado do projeto:

```powershell
winget install --id astral-sh.uv --exact
uv python install 3.12
uv sync --locked
```

Copie os arquivos locais, ambos ignorados pelo Git:

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

Banco e sessão do Telethon ficam, por padrão, em `%USERPROFILE%\.promo_bot`, fora do repositório.
Outro diretório externo pode ser definido em `PROMO_BOT_RUNTIME_DIR`.

Mantenha estes controles:

```env
DRY_RUN=true
PUBLISH_REAL_DEALS=false
SEARCH_ENABLED=false
PUBLISH_WITHOUT_AFFILIATE=false
COUPON_BROWSER_VERIFICATION=false
```

## CLI

Comandos locais e offline:

```powershell
uv run promo-bot validate-config
uv run promo-bot doctor
uv run promo-bot init-db
uv run promo-bot run
uv run promo-bot send-test
```

`send-test` somente mostra o preview. `send-test --live` é uma operação externa distinta e envia
exclusivamente a mensagem sintética fixa, nunca uma promoção persistida.

Depois de configurar as credenciais localmente e os canais no `config.yaml`:

```powershell
uv run promo-bot listen --authorize
uv run promo-bot listen
```

O primeiro comando solicita telefone, código e eventual senha 2FA interativamente. Esses valores não
devem ser incluídos em `.env`, argumentos, logs ou conversas.

Consulte [docs/TELEGRAM_RELAY.md](docs/TELEGRAM_RELAY.md) antes de qualquer teste real.

## Qualidade offline

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

O pytest padrão exclui testes `live` e `browser` e bloqueia sockets externos. O loopback é permitido
somente porque o event loop assíncrono do Windows usa um socket interno para acordar o próprio loop.

Consulte também [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) e
[docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md).
