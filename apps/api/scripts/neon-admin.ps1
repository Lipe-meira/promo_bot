<#
Runs `promo-api-admin` (invite | revoke | list) against the managed (Neon) database. Run from apps/api,
by the operator.

The connection string is typed into a protected prompt (no echo, not an argument, not in history),
exported only to the child process that needs it, and removed in `finally`. The e-mail address is
piped to standard input (never an argument), so it stays out of the process list.

  ./scripts/neon-admin.ps1 -Command invite -Email someone@example.com
  ./scripts/neon-admin.ps1 -Command list
#>
param(
    [Parameter(Mandatory)] [ValidateSet("invite", "revoke", "list")] [string] $Command,
    [string] $Email
)
$ErrorActionPreference = "Stop"
if ($Command -ne "list" -and -not $Email) { throw "-Email is required for '$Command'." }
$secure = Read-Host "Direct Neon URL (postgresql+asyncpg://...)" -AsSecureString
try {
    $env:PROMO_API_DATABASE_URL = [System.Net.NetworkCredential]::new("", $secure).Password
    if ($Command -eq "list") {
        uv run promo-api-admin list
    }
    else {
        $Email | uv run promo-api-admin $Command
    }
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:PROMO_API_DATABASE_URL -ErrorAction SilentlyContinue
    $secure.Dispose()
}
