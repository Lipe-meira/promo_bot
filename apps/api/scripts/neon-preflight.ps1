<#
Read-only preflight of the managed (Neon) database. Run from apps/api, by the operator.

The connection string is typed into a protected prompt (no echo, not an argument, not in history),
exported only to the child process that needs it, and removed in `finally`. Nothing is written.

  ./scripts/neon-preflight.ps1 -DbHost ep-xxxx.us-east-2.aws.neon.tech -Database <db> -Role <role>

Type the expected host, database and role yourself (do not copy them from the variable).
#>
param(
    [Parameter(Mandatory)] [string] $DbHost,
    [Parameter(Mandatory)] [string] $Database,
    [Parameter(Mandatory)] [string] $Role,
    [string] $Schema = "public"
)
$ErrorActionPreference = "Stop"
$secure = Read-Host "Direct Neon URL (postgresql+asyncpg://...)" -AsSecureString
try {
    $env:PROMO_API_DATABASE_URL = [System.Net.NetworkCredential]::new("", $secure).Password
    uv run python -m promo_api.preflight --host $DbHost --database $Database --role $Role --schema $Schema
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:PROMO_API_DATABASE_URL -ErrorAction SilentlyContinue
    $secure.Dispose()
}
