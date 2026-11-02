$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local-run\processes.json'
if (!(Test-Path -LiteralPath $statePath)) { Write-Output 'No recorded local processes.'; exit }
$records = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
foreach ($record in $records) {
    if ($record.external -or !$record.pid) { continue }
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    # Protect against PID reuse: stop only the exact processes launched by start-local.
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$record.started).UtcDateTime.Ticks) {
        # The Windows venv launcher has a Python child. Stop its entire tree so
        # a restart cannot leave an orphaned API or worker holding the lock.
        & taskkill.exe /PID $record.pid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Unable to stop $($record.name). Keep process record for diagnosis." }
        Write-Output "Stopped $($record.name) ($($record.pid))"
    }
}
Remove-Item -LiteralPath $statePath
