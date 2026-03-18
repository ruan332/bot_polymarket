param(
  [string]$TargetPath = ".env"
)

$content = @"
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=

DATABASE_URL=postgresql://trading:trading@localhost:5432/trading
REDIS_URL=redis://localhost:6379/0

POLYMARKET_PRIVATE_KEY=
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
POLYMARKET_FUNDER=
POLYMARKET_SIGNATURE_TYPE=0
POLYMARKET_CHAIN_ID=137

LIVE_TRADING=false
SMOKE_TEST_MODE=true
ENVIRONMENT=development
MAX_DAILY_SPEND_USD=5.00
MAX_SINGLE_POSITION_USD=100.00
PAPER_BANKROLL_USD=1000.00
AGENT_HEARTBEAT_TTL_SECONDS=45
"@

if (Test-Path $TargetPath) {
  Write-Host "$TargetPath already exists; leaving it unchanged."
  exit 0
}

Set-Content -Path $TargetPath -Value $content -Encoding UTF8
Write-Host "Created $TargetPath with localhost defaults and SMOKE_TEST_MODE=true"
