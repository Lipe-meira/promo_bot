# Piloto privado com tracking comparado

O contrato anexado de `link.generate` mostra `resp_result.result.tracking_id`
textual nos exemplos de resposta; sua presença obrigatória na resposta não é
declarada. O bot exige esse campo e igualdade exata com o tracking configurado
como política local de confirmação. Ausência/nulo/vazio recebe
`ALIEXPRESS_TRACKING_UNCONFIRMED`, tipo inválido recebe
`ALIEXPRESS_TRACKING_RESPONSE_INVALID` e texto divergente recebe
`ALIEXPRESS_TRACKING_MISMATCH`. Nenhum desses casos produz prova aceita ou preview.

Provas canônicas novas usam `top-link-generate-tracking-v2`. Cache e entrega,
inclusive de múltiplos produtos, recusam provas de versões anteriores. Os
registros antigos permanecem armazenados e não recebem confirmação retroativa.
Não há migration ou histórico durável novo. O parser continua correlacionando
canônicos pelo produto; o parser separado dos shorts mantém source exato ou
`POSITIONAL_SINGLETON`, sempre com tracking exatamente correspondente.

Isso confirma somente o tracking devolvido pela API. Comissão efetiva e a
precedência sobre atribuição interna do short dependem de relatórios do Portals.
Preços e descontos no texto são informações da origem, sem validação pelo bot.

## Comando futuro, não executado na implementação

Use a worktree revisada abaixo, um único canal-fonte numérico já autorizado no
`config.yaml` e o mesmo banco dedicado. Desligue antes o modo automático antigo
e seus agendamentos. A checagem de processos não impede início posterior por um
agendamento ou outra máquina. O lock cobre processos atualizados sobre o mesmo
banco; bancos diferentes e versões antigas não têm deduplicação cruzada garantida.
O listener recebe só mensagens novas depois de estar pronto. Shorts precisam
ser a única URL visível. Ambos os caminhos compartilham os tetos de 10 mensagens,
10 chamadas TOP e 10 envios; a janela de admissão é de até 900 segundos, com
finalização limitada de trabalho já admitido. Envio incerto não é reenviado
automaticamente. Não existe publicação no canal público.

A pré-checagem e a execução carregam explicitamente o mesmo `.env` absoluto.
Overrides de ambiente divergentes, inclusive vazios, bloqueiam antes de ativar
os gates. A pré-checagem exibe apenas comparações booleanas, nunca valores ou
exceções de configuração. Os gates são desligados no `finally`.

```powershell
cd F:\projetos\promo_bot

$pilotProject = 'C:\Users\felip\.codex\worktrees\canonical-tracking-validation\promo_bot'
$envFile = 'F:\projetos\promo_bot\.env'
$configPath = 'F:\projetos\promo_bot\config.yaml'
$pilotDb = Join-Path $env:LOCALAPPDATA 'promo_bot\shadow\aliexpress-coin-listener-pilot.sqlite3'

$pilotGates = @(
    'ALIEXPRESS_LIVE_API_ENABLED'
    'ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED'
    'ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED'
    'ALIEXPRESS_TELEGRAM_SHADOW_ENABLED'
    'ALIEXPRESS_TELEGRAM_SHADOW_LISTENER_ENABLED'
    'TELEGRAM_SHADOW_TEST_DELIVERY_ENABLED'
    'ALIEXPRESS_DISCOVERY_SHADOW_ENABLED'
    'ALIEXPRESS_DISCOVERY_HOTPRODUCT_SHADOW_ENABLED'
    'ALIEXPRESS_DISCOVERY_SKU_SHADOW_ENABLED'
    'ALIEXPRESS_SKU_DIMENSION_API_CONFIRMED'
    'PUBLISH_REAL_DEALS'
    'PUBLISH_WITHOUT_AFFILIATE'
    'SEARCH_ENABLED'
    'COUPON_BROWSER_VERIFICATION'
)

$pilotEntry = @'
import inspect
import json
import os
import sys
from pathlib import Path

try:
    from promo_bot.config.settings import EnvironmentSettings
    from promo_bot.providers.aliexpress.parsing import parse_link_generate
    from promo_bot.providers.aliexpress.contracts import (
        LINK_GENERATE_TRACKING_CONFIRMED_CONTRACT_VERSION,
    )

    env_path = Path(sys.argv[1])
    if not env_path.is_absolute() or not env_path.is_file():
        raise ValueError()
    if "expected_tracking_id" not in inspect.signature(parse_link_generate).parameters:
        raise ValueError()
    if LINK_GENERATE_TRACKING_CONFIRMED_CONTRACT_VERSION != "top-link-generate-tracking-v2":
        raise ValueError()

    EnvironmentSettings.model_config["env_file"] = str(env_path)
    settings = EnvironmentSettings()
    tracking = settings.aliexpress_tracking_id
    effective_matches = (
        tracking is not None
        and tracking.get_secret_value() == "promo_bot_br"
    )
    overrides = [
        value
        for key, value in os.environ.items()
        if key.casefold() == "aliexpress_tracking_id"
    ]
    override_divergent = any(value != "promo_bot_br" for value in overrides)
    report = {
        "effective_tracking_matches": effective_matches,
        "override_present": bool(overrides),
        "override_divergent": override_divergent,
    }
except Exception:
    print(json.dumps({"preflight_ok": False}))
    raise SystemExit(2)

allowed = effective_matches and not override_divergent
if sys.argv[2:] == ["--preflight"] or not allowed:
    print(json.dumps(report, sort_keys=True))
if not allowed:
    raise SystemExit(2)
if sys.argv[2:] == ["--preflight"]:
    raise SystemExit(0)

from promo_bot.cli import main
raise SystemExit(main(sys.argv[2:]))
'@

try {
    foreach ($pilotGate in $pilotGates) {
        Set-Item -Path "Env:$pilotGate" -Value 'false'
    }
    $env:DRY_RUN = 'true'

    foreach ($pilotFile in @($envFile, $configPath, $pilotDb)) {
        if (-not (Test-Path -LiteralPath $pilotFile -PathType Leaf)) {
            throw 'Arquivo necessário ao piloto não encontrado.'
        }
    }

    $pilotBranch = git -C $pilotProject branch --show-current
    if ($LASTEXITCODE -ne 0 -or $pilotBranch -ne 'codex/canonical-tracking-validation') {
        throw 'Worktree de implementação incorreta.'
    }

    uv run --offline --no-sync --project $pilotProject python -c $pilotEntry $envFile --preflight
    if ($LASTEXITCODE -ne 0) {
        throw 'Pré-checagem sanitizada de tracking falhou.'
    }

    $activeListeners = @(Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -match '(?i)(shadow-auto-deliver|shadow-listen|coin-shadow-auto-deliver|promo[_-]bot.*\b(run|listen)\b)'
        })
    if ($activeListeners.Count -gt 0) {
        $activeListeners | Select-Object ProcessId, Name
        throw 'Listener anterior detectado; encerre-o antes do piloto.'
    }

    $env:ALIEXPRESS_LIVE_API_ENABLED = 'true'
    $env:ALIEXPRESS_COIN_SHORT_SHADOW_ENABLED = 'true'
    $env:ALIEXPRESS_TELEGRAM_SHADOW_AUTO_DELIVERY_ENABLED = 'true'

    uv run --offline --no-sync --project $pilotProject python -c $pilotEntry $envFile `
        aliexpress shadow-auto-deliver `
        --config $configPath `
        --shadow-database $pilotDb `
        --destination private-test `
        --include-coin-shorts `
        --max-messages 10 `
        --run-seconds 900 `
        --max-api-calls 10 `
        --max-links-per-message 1 `
        --max-send-messages 10

    if ($LASTEXITCODE -ne 0) {
        throw "Piloto encerrou com código $LASTEXITCODE."
    }
}
finally {
    foreach ($pilotGate in $pilotGates) {
        Set-Item -Path "Env:$pilotGate" -Value 'false'
    }
    $env:DRY_RUN = 'true'
}
```
