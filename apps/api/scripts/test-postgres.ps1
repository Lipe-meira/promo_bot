# Runs the PostgreSQL test suite against a throwaway container and removes it afterwards.
#
# Nothing here touches a real database: the container is created for this run, listens only on
# 127.0.0.1 with a random port, keeps its data in memory (tmpfs) and is deleted in `finally`.
# Requires Docker. Usage (from apps/api):  ./scripts/test-postgres.ps1 [extra pytest args]

$ErrorActionPreference = 'Stop'
$image = if ($env:PROMO_API_TEST_POSTGRES_IMAGE) { $env:PROMO_API_TEST_POSTGRES_IMAGE } else { 'postgres:17-alpine' }
$name = "promo-api-test-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$password = [guid]::NewGuid().ToString('N')
$database = 'promo_api_test'
$apiRoot = Split-Path -Parent $PSScriptRoot
$exitCode = 1

docker run --detach --rm --name $name `
  --publish '127.0.0.1::5432' `
  --tmpfs /var/lib/postgresql/data `
  --env "POSTGRES_PASSWORD=$password" --env "POSTGRES_DB=$database" `
  $image | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'could not start the ephemeral PostgreSQL container' }

try {
  $port = (docker port $name 5432/tcp | Select-Object -First 1).Split(':')[-1]
  $ready = $false
  foreach ($attempt in 1..60) {
    # cmd.exe keeps Windows PowerShell 5.1 from turning native stderr into a terminating error.
    cmd /c "docker exec $name pg_isready -h 127.0.0.1 -U postgres -d $database >nul 2>&1"
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
  }
  if ($ready) { Start-Sleep -Seconds 1 }  # the image restarts once after initialization
  if (-not $ready) { throw 'the ephemeral PostgreSQL did not become ready' }

  $env:PROMO_API_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:$password@127.0.0.1:$port/$database"
  Push-Location $apiRoot
  try {
    uv run pytest -m postgres @args
    $exitCode = $LASTEXITCODE
  } finally {
    Pop-Location
  }
} finally {
  Remove-Item Env:PROMO_API_TEST_DATABASE_URL -ErrorAction SilentlyContinue
  cmd /c "docker rm --force $name >nul 2>&1"
}
exit $exitCode
