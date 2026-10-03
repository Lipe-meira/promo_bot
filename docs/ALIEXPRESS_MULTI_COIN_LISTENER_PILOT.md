# Listener shadow: múltiplos shorts AliExpress

Extensão manual e opt-in do piloto privado. **Não habilita publicação**, não resolve
shorts e não executa o teste descrito abaixo automaticamente. O singleton e os
comandos manuais antigos continuam disponíveis.

## Contrato e limites

`aliexpress shadow-auto-deliver --include-coin-shorts --allow-multiple-coin-shorts`
aceita de uma a três **ocorrências** de URL, limitado por `--max-links-per-message`.
Aumentar o limite sem a nova flag não habilita múltiplos shorts. Uma URL canônica
isolada continua aceita; duas ou mais URLs exigem exclusivamente shorts estritos:

- `https://a.aliexpress.com/_[A-Za-z0-9]{7,8}`
- `https://s.click.aliexpress.com/e/_[A-Za-z0-9]{7,8}`

Sem query, fragmento, porta, userinfo, barra final ou host alternativo. Misturas,
URLs extras/ocultas, botões, mídia, entidades inseguras e spans incoerentes são
recusados antes de TOP. Entidades URL usam offsets UTF-16 convertidos para índices
Python; nunca se usam offsets Telegram diretamente para substituir texto.

Cada short **literal distinto** sem cache exige sua própria chamada singleton,
uma tentativa, zero retries e zero redirects. O parser singleton e sua validação
exata de tracking não mudam. Rótulos APP/PC são somente texto, não evidência de
destino. O renderer substitui todas as ocorrências em uma passagem, preservando
emojis, CRLF, espaços, preços e cupons fora dos spans. O texto final deve caber
em 4096 unidades UTF-16; não há fragmentação em várias mensagens.

## Cache, histórico e falhas

O cache válido mantém o contrato, tracking, contexto de chave e TTL de 24 horas
atuais. A expiração do preview agregado é o menor prazo de suas evidences, não
24 horas adicionais a partir do cache hit. A existência do histórico não autoriza
reutilizar um link expirado. Não há confirmação retroativa de legado nem consumo
automático de solicitações `--generation-request` pelo listener.

A inspeção de cache não faz claim, recovery, purge ou rede. A recuperação de estado
é separada e não retoma mensagens antigas. Se os misses conhecidos excederem o
saldo TOP, a mensagem é recusada antes de qualquer geração com
`ALIEXPRESS_COIN_MESSAGE_API_BUDGET_INSUFFICIENT`. A mensagem conta no limite de
mensagens, mas não consome TOP; uma mensagem menor pode usar o saldo restante.

Inspeção não reserva cache. Expiração ou concorrência posteriores podem causar
trabalho parcial: gerações já confirmadas permanecem no histórico, mas nenhuma
mensagem parcial, preview incompleto ou envio é produzido. O primeiro erro para
as entradas restantes. Claim ativo termina imediatamente, sem polling; timeout
ou chamada interrompida continua bloqueada como `UNCERTAIN`, sem retry automático.

Um PREVIEW e um único SEND associam todas as gerações distintas. `A/B/A` preserva
três ordinais no `origin.occurrences`, mas somente duas associações por uso.
`cache_hit` pertence à associação geração/uso, não à posição. Esse mapa sobrevive
ao purge operacional. Reserva, SEND e associações são persistidos antes de envio;
`SEND_IN_FLIGHT` é persistido antes de `sendMessage`. Falha de persistência impede
envio. Resultado incerto não reabre a reserva.

O purge elimina o preview completo se qualquer evidence necessária expirar,
anula seus ponteiros na reserva, remove ocorrências e somente então a evidence.
Reservas e histórico durável permanecem. Não se apagam confirmações externas
quando outra entrada falha.

## Schema e isolamento

Migration aditiva `b8c2e4f6a901`, sobre `9b3d5e7f1a20`:

- `aliexpress_coin_shadow_multi_previews`;
- `aliexpress_coin_shadow_multi_preview_occurrences`;
- `multi_preview_id` opcional na reserva coin existente, mutuamente exclusivo
  com `preview_id` e `ON DELETE SET NULL`.

Ocorrências usam FK `RESTRICT` para evidence READY e geração durável; preview usa
cascade somente para suas ocorrências. A unicidade da reserva por mensagem/destino
não muda. Downgrade recusa conteúdo do novo caminho, inclusive usos duráveis após
purge, com `COIN_MULTI_DOWNGRADE_BLOCKED_NONEMPTY`, antes de qualquer DDL.
O opt-in exige schema atualizado antes de construir transportes. Nenhum upgrade
é feito implicitamente pelo listener, nem há escrita em outro banco ou produção.

## Contadores

O relatório usual permanece sanitizado. Apenas com opt-in aparece `coin_multi`:

| Campo | Semântica |
|---|---|
| `occurrences_admitted` | Soma de ocorrências em mensagens inteiramente elegíveis |
| `distinct_inputs_admitted` | Soma de entradas distintas **por mensagem**, não unicidade do run |
| `cache_distinct_inputs` | Entradas efetivamente reutilizadas de cache |
| `generated_distinct_inputs_confirmed` | Novas gerações confirmadas, mesmo se outra entrada falhar |
| `in_message_reuses` | Ocorrências menos entradas distintas |
| `all_cache_messages` | Previews completos com todas as entradas em cache |
| `partial_cache_messages` | Previews completos com cache parcial |

`cache_hits` continua sendo por mensagem completa, não por ocorrência. Repetir
localmente um short recém-gerado não é cache hit. `api_calls` e `send_messages`
contam vagas consumidas pelos hooks antes da marcação durável/rede: uma falha de
persistência nessa janela pode consumir uma vaga sem chamada externa. Consulte
também os fatos do histórico; os contadores não provam conclusão da rede.

## Piloto futuro — não executado nesta implementação

Exige autorização posterior para criar/migrar **somente um banco novo** e para
as chamadas live. Execute na worktree desta feature antes de merge; após integração,
substitua `$pilotProject` e a branch esperada pela worktree limpa de main atualizada.
Os bancos anteriores são preservados. O arquivo Python temporário abaixo resolve
a passagem de aspas do PowerShell, mas não guarda credenciais.

Uma fonte numérica já autorizada e destino apenas `private-test`. Desligue o modo
automático antigo. A inspeção de processos é complementar ao lock do banco;
bancos diferentes, versões antigas e outras máquinas não têm deduplicação cruzada.

1. Envie A novo, sozinho.
2. Após entrega privada, envie A + B novo: cache parcial.
3. Após entrega, repita literalmente A + B: cache total.
4. Após entrega, envie A + B + A: três ocorrências e duas entradas.
5. Após entrega, envie A + URL canônica: rejeição antes de TOP, sem fallback.

Envie somente após `Telegram listener connected` e aguarde brevemente. O handler
já está instalado nesse ponto; a admissão é marcada logo a seguir, sem await
intermediário. O cronômetro inclui startup; não envie outras mensagens durante
o teste. Com A/B novos, válidos e dentro do TTL, o esperado é 2 TOP, 2 gerações,
4 PREVIEW, 4 SEND, 4 envios, 2 cache hits completos, 1 parcial e 1 rejeição.
Conte SEND por UUID distinto, pois ele aparece associado a várias gerações.

```powershell
cd C:\Users\felip\.codex\worktrees\multi-coin-short-shadow\promo_bot

$pilotProject = 'C:\Users\felip\.codex\worktrees\multi-coin-short-shadow\promo_bot'
$envFile = 'F:\projetos\promo_bot\.env'
$configPath = 'F:\projetos\promo_bot\config.yaml'
$expectedSource = '-1004376233064'
$pilotRoot = Join-Path $env:LOCALAPPDATA 'promo_bot\shadow\multi-short-pilot'
$pilotDb = Join-Path $pilotRoot ('validation-' + [guid]::NewGuid().ToString('N') + '.sqlite3')
$pilotDbUrl = 'sqlite+aiosqlite:///' + $pilotDb.Replace('\', '/')
$previousDatabaseUrl = [Environment]::GetEnvironmentVariable('PROMO_BOT_DATABASE_URL', 'Process')
$pilotGates = @(
    'ALIEXPRESS_LIVE_API_ENABLED', 'ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED',
    'ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED', 'ALIEXPRESS_TELEGRAM_SHADOW_ENABLED',
    'ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED', 'TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED',
    'ALIEXPRESS_DISCOVERY_SHADOW_ENABLED', 'ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_ENABLED',
    'ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED', 'ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED',
    'MERCADO_LIVRE_BROWSER_ENABLED', 'PUBLISH_REAL_DEALS', 'PUBLISH_WITHOUT_AFFILIATE',
    'SEARCH_ENABLED', 'COUPON_BROWSER_VERIFICATION'
)
$pilotScript = Join-Path ([IO.Path]::GetTempPath()) (
    'promo-bot-multi-short-' + [guid]::NewGuid().ToString('N') + '.py'
)
$scriptOwned = $false
$pilotCompleted = $false
$pilotStartedAt = $null
$pilotEntry = @'
import inspect
import json
import os
import sys
from pathlib import Path

try:
    from promo_bot.cli import command_aliexpress_shadow_auto_delivery, main
    from promo_bot.config.loader import load_app_config
    from promo_bot.config.settings import EnvironmentSettings

    if "allow_multiple_coin_shorts" not in inspect.signature(
        command_aliexpress_shadow_auto_delivery
    ).parameters:
        raise ValueError()
    env_path, config_path = Path(sys.argv[1]), Path(sys.argv[2])
    expected_source, command = sys.argv[3], sys.argv[4:]
    if not all(p.is_absolute() and p.is_file() for p in (env_path, config_path)):
        raise ValueError()
    EnvironmentSettings.model_config["env_file"] = str(env_path)
    settings, config = EnvironmentSettings(), load_app_config(config_path)
    tracking = settings.aliexpress_tracking_id
    tracking_matches = tracking is not None and tracking.get_secret_value() == "promo_bot_br"
    overrides = [value for key, value in os.environ.items()
                 if key.casefold() == "aliexpress_tracking_id"]
    override_divergent = any(value != "promo_bot_br" for value in overrides)
    destination = config.telegram_shadow_delivery.allowed_destinations.get("private-test")
    source_matches = config.source_channels == (expected_source,)
    destination_allowed = (destination is not None and destination.kind == "private_channel"
                           and destination.chat_id not in config.source_channels)
    provider = config.providers.get("aliexpress")
    provider_ready = provider is not None and provider.enabled and provider.affiliate_mode == "official_api"
    safe = settings.dry_run and not any((
        settings.publish_real_deals, settings.publish_without_affiliate, settings.search_enabled,
        settings.coupon_browser_verification, settings.aliexpress_telegram_shadow_enabled,
        settings.aliexpress_telegram_shadow_listener_enabled, settings.telegram_shadow_test_delivery_enabled,
    ))
    def present(value):
        return value is not None and bool(value.get_secret_value())
    credentials_ready = bool(settings.telegram_api_id) and all(present(value) for value in (
        settings.telegram_api_hash, settings.telegram_bot_token, settings.aliexpress_app_key,
        settings.aliexpress_app_secret, settings.aliexpress_tracking_id,
    ))
    allowed = all((tracking_matches, not override_divergent, source_matches,
                   destination_allowed, provider_ready, safe, credentials_ready))
    report = {"preflight_ok": allowed, "effective_tracking_matches": tracking_matches,
              "override_present": bool(overrides), "override_divergent": override_divergent,
              "source_matches": source_matches, "destination_allowed": destination_allowed,
              "credentials_ready": credentials_ready}
except Exception:
    print(json.dumps({"preflight_ok": False, "error_code": "MULTI_PILOT_PREFLIGHT_FAILED"}))
    raise SystemExit(2)
if command == ["--preflight"] or not allowed:
    print(json.dumps(report, sort_keys=True))
if not allowed:
    raise SystemExit(2)
if command == ["--preflight"]:
    raise SystemExit(0)
try:
    result = main(command)
except Exception:
    print(json.dumps({"status": "failed_safe", "error_code": "MULTI_PILOT_WRAPPER_FAILED"}))
    raise SystemExit(2)
raise SystemExit(result)
'@

try {
    foreach ($gate in $pilotGates) { Set-Item -Path "Env:$gate" -Value 'false' }
    $env:DRY_RUN = 'true'
    foreach ($file in @($envFile, $configPath)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw 'Configuração necessária não encontrada.'
        }
    }
    $branch = git -C $pilotProject branch --show-current
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'codex/multi-coin-short-shadow') {
        throw 'Worktree incorreta.'
    }
    $status = @(git -C $pilotProject status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $status.Count -gt 0) { throw 'Worktree não está limpa.' }
    $listeners = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -match '(?i)(shadow-auto-deliver|shadow-listen|coin-shadow-auto-deliver|promo[_-]bot.*\b(run|listen)\b)'
    })
    if ($listeners.Count -gt 0) {
        $listeners | Select-Object ProcessId, Name
        throw 'Listener anterior detectado; encerre-o antes do piloto.'
    }
    if (Test-Path -LiteralPath $pilotScript) { throw 'Script temporário existente; preservado.' }
    $scriptOwned = $true
    Set-Content -LiteralPath $pilotScript -Value $pilotEntry -Encoding UTF8 -ErrorAction Stop
    uv run --offline --no-sync --no-env-file --project $pilotProject `
        python $pilotScript $envFile $configPath $expectedSource --preflight
    if ($LASTEXITCODE -ne 0) { throw 'Pré-checagem falhou; não iniciar migration ou listener.' }
    if (Test-Path -LiteralPath $pilotDb) { throw 'Banco existente; não será migrado.' }
    New-Item -ItemType Directory -Path $pilotRoot -Force -ErrorAction Stop | Out-Null
    $env:PROMO_BOT_DATABASE_URL = $pilotDbUrl
    uv run --offline --no-sync --no-env-file --project $pilotProject `
        python $pilotScript $envFile $configPath $expectedSource init-db --database-url $pilotDbUrl
    if ($LASTEXITCODE -ne 0) { throw 'Migration do banco novo falhou; não iniciar listener.' }
    if ($null -eq $previousDatabaseUrl) {
        Remove-Item Env:PROMO_BOT_DATABASE_URL -ErrorAction SilentlyContinue
    } else { $env:PROMO_BOT_DATABASE_URL = $previousDatabaseUrl }
    Write-Host "Banco permanente do piloto: $pilotDb"
    $pilotStartedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $env:ALIEXPRESS_LIVE_API_ENABLED = 'true'
    $env:ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED = 'true'
    $env:ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED = 'true'
    uv run --offline --no-sync --no-env-file --project $pilotProject `
        python $pilotScript $envFile $configPath $expectedSource `
        aliexpress shadow-auto-deliver --config $configPath --shadow-database $pilotDb `
        --destination private-test --include-coin-shorts --allow-multiple-coin-shorts `
        --max-links-per-message 3 --max-messages 5 --run-seconds 600 `
        --max-api-calls 5 --max-send-messages 5
    $pilotExitCode = $LASTEXITCODE
    $pilotCompleted = $true
    if ($pilotExitCode -ne 0) { Write-Warning 'Falha no piloto. Não repetir automaticamente.' }
}
finally {
    foreach ($gate in $pilotGates) { Set-Item -Path "Env:$gate" -Value 'false' }
    $env:DRY_RUN = 'true'
    if ($null -eq $previousDatabaseUrl) {
        Remove-Item Env:PROMO_BOT_DATABASE_URL -ErrorAction SilentlyContinue
    } else { $env:PROMO_BOT_DATABASE_URL = $previousDatabaseUrl }
    if ($scriptOwned -and (Test-Path -LiteralPath $pilotScript -PathType Leaf)) {
        $children = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.Contains($pilotScript)
        })
        if ($children.Count -gt 0) { Wait-Process -Id $children.ProcessId -ErrorAction Stop }
        Remove-Item -LiteralPath $pilotScript -ErrorAction Stop
    }
}
if ($pilotCompleted) {
    uv run --offline --no-sync --no-env-file --project $pilotProject `
        promo-bot affiliate link-history list --database $pilotDb --scope shadow `
        --platform aliexpress --limit 200
    uv run --offline --no-sync --no-env-file --project $pilotProject `
        promo-bot affiliate link-history list --database $pilotDb --scope shadow `
        --platform aliexpress --used-after $pilotStartedAt --limit 200
    foreach ($sendResult in @('SEND_CONFIRMED', 'SEND_UNCERTAIN', 'SEND_FAILED')) {
        Write-Host "Resultado de envio: $sendResult"
        uv run --offline --no-sync --no-env-file --project $pilotProject `
            promo-bot affiliate link-history list --database $pilotDb --scope shadow `
            --platform aliexpress --send-result $sendResult --used-after $pilotStartedAt --limit 200
    }
}
```

`--offline` restringe o uv, **não** a rede do bot quando os gates estiverem ativos.
Não executar antes da autorização separada. Logs/saída automática não mostram
conteúdo, shorts, tracking ou URLs gerados. A consulta padrão oculta URLs.

Tracking confirmado não comprova comissão nem precedência sobre contexto herdado
do short. Preços, descontos, moedas e cupons são informações da origem, não
validados pelo bot. Experiência APP/PC/Moedas requer comparação manual separada.
A pendência documental anterior do piloto não é resolvida por esta extensão.
