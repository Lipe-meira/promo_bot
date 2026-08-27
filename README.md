# Promo Affiliate Bot

Fundação local e segura de um bot de promoções e links de afiliado. O projeto é preparado para
Windows, Python 3.12, execução assíncrona e evolução por fases.

## Estado atual

Somente a **Fase 1 — Fundação** está implementada:

- configuração tipada de `.env` e YAML;
- modelos de domínio com `Decimal` e timestamps UTC;
- SQLite assíncrono, SQLAlchemy 2.x e Alembic;
- logs JSON sanitizados;
- CLI de diagnóstico, validação, migração e dry-run;
- testes offline, Ruff e mypy.

Ainda não existem listener ou envio por Telegram, providers de lojas, geração de links de afiliado,
acesso à internet, verificação de cupons, Playwright, scheduler ou descoberta de ofertas. Nenhum
comando desta fase simula essas capacidades.

## Requisitos no Windows

- Git;
- WinGet;
- [uv](https://docs.astral.sh/uv/), instalado com:

```powershell
winget install --id astral-sh.uv --exact
```

O projeto fixa Python 3.12 em `.python-version`. O uv pode instalar essa versão isoladamente sem
remover outras versões do Python:

```powershell
uv python install 3.12
uv sync --locked
```

Se o terminal aberto antes da instalação ainda não encontrar `uv`, abra um novo PowerShell.

## Configuração local

Copie os exemplos sem alterar os arquivos versionados:

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

Preencha apenas as credenciais que forem necessárias em fases futuras. A Fase 1 funciona sem
credenciais. `.env` e `config.yaml` são ignorados pelo Git.

Por padrão, banco, logs e demais dados de runtime ficam em `%USERPROFILE%\.promo_bot`, fora do
repositório. É possível configurar outro local com `PROMO_BOT_RUNTIME_DIR`. Apenas URLs SQLite
assíncronas são aceitas nesta fase.

Defaults de segurança:

```env
DRY_RUN=true
SEARCH_ENABLED=false
PUBLISH_WITHOUT_AFFILIATE=false
COUPON_BROWSER_VERIFICATION=false
```

## CLI da Fase 1

```powershell
uv run promo-bot validate-config
uv run promo-bot doctor
uv run promo-bot init-db
uv run promo-bot run
```

Também é possível usar o wrapper:

```powershell
.\run.ps1 doctor
```

`run` apenas confirma, por log estruturado, que a fundação está pronta em dry-run. Ele recusa a
execução quando `DRY_RUN=false`.

## Qualidade

Os testes padrão não usam rede, serviços reais, Telegram, navegador, credenciais nem APIs pagas:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Os markers `integration`, `live` e `browser` estão registrados. Testes `live` e `browser` deverão
continuar desativados por padrão quando forem introduzidos em fases autorizadas.

## Agendador de Tarefas do Windows

Uma tarefa poderá futuramente chamar `run.ps1` com o diretório inicial configurado para a raiz do
repositório. Não agende `promo-bot run` nesta fase: listener e scheduler pertencem a fases futuras.

Consulte [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) e
[docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) para decisões e escopo completo.
