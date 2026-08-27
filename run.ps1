param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PromoBotArguments
)

$ErrorActionPreference = "Stop"
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uvCommand) {
    $packageRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $uvExecutable = Get-ChildItem -Path $packageRoot -Filter "uv.exe" -Recurse -ErrorAction SilentlyContinue |
        Where-Object FullName -Like "*astral-sh.uv*" |
        Select-Object -First 1 -ExpandProperty FullName

    if (-not $uvExecutable) {
        throw "uv was not found. Install it with: winget install --id astral-sh.uv --exact"
    }
} else {
    $uvExecutable = $uvCommand.Source
}

& $uvExecutable run promo-bot @PromoBotArguments
exit $LASTEXITCODE
