param(
  [switch]$StartServices,
  [string]$PgBinDir = "",
  [string]$RedisServerPath = ""
)

if (-not (Test-Path ".env")) {
  & "$PSScriptRoot\Create-LocalEnv.ps1"
}

if ($StartServices) {
  & "$PSScriptRoot\Start-LocalPostgres.ps1" -PgBinDir $PgBinDir
  & "$PSScriptRoot\Start-LocalRedis.ps1" -RedisServerPath $RedisServerPath
}

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = (Get-Command python -ErrorAction Stop).Source
}
$api = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","api.main:app","--host","127.0.0.1","--port","8000" -PassThru -WindowStyle Hidden
$agents = Start-Process -FilePath $python -ArgumentList "run_agents.py" -PassThru -WindowStyle Hidden

try {
  Start-Sleep -Seconds 6
  $status = Invoke-RestMethod "http://127.0.0.1:8000/agents/status"
  $metrics = Invoke-RestMethod "http://127.0.0.1:8000/metrics/overview"
  $signals = Invoke-RestMethod "http://127.0.0.1:8000/signals/recent"
  Write-Host "agents:" ($status | ConvertTo-Json -Compress)
  Write-Host "metrics:" ($metrics | ConvertTo-Json -Compress)
  Write-Host "signals_count:" $signals.Count
}
finally {
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
  if ($agents -and -not $agents.HasExited) { Stop-Process -Id $agents.Id -Force }
}
