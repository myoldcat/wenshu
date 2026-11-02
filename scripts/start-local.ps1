param(
    [string]$FrontendPath = '',
    [string]$DataPath = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (!$FrontendPath) { $FrontendPath = Join-Path $projectRoot 'frontend' }
$pythonPath = Join-Path $projectRoot 'server\.venv\Scripts\python.exe'
$nodePath = (Get-Command node -ErrorAction Stop).Source
if (!(Test-Path -LiteralPath $pythonPath)) { throw '请先按 server/README.md 安装本地 Python 环境。' }
if (!(Test-Path -LiteralPath (Join-Path $FrontendPath 'node_modules\vite\bin\vite.js'))) { throw '前端路径缺少 Vite 依赖。' }
$runDir = Join-Path $projectRoot '.local-run'
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$statePath = Join-Path $runDir 'processes.json'
$existing = if (Test-Path -LiteralPath $statePath) { Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json } else { @() }
$records = [System.Collections.Generic.List[object]]::new()
if (!$DataPath) { $DataPath = if ($env:WENSHU_DATA_DIR) { $env:WENSHU_DATA_DIR } else { Join-Path $projectRoot 'data\wenshu' } }
$env:WENSHU_DATA_DIR = [System.IO.Path]::GetFullPath($DataPath)
$env:PYTHONUTF8 = '1'
function Save-LocalState {
    @($records.ToArray()) | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8
}
function Test-LocalEndpoint($componentName) {
    try {
        if ($componentName -eq 'api') {
            $health = Invoke-RestMethod 'http://127.0.0.1:8787/api/health' -TimeoutSec 2
            return ($health.application -eq 'wenshu' -and $health.data_dir -eq $env:WENSHU_DATA_DIR)
        }
        if ($componentName -eq 'frontend') {
            $source = Invoke-WebRequest 'http://127.0.0.1:4173/src/App.jsx' -TimeoutSec 2
            return ($source.StatusCode -eq 200 -and $source.Content.Contains('useDocumentLibrary'))
        }
    } catch { return $false }
    return $true
}
function Start-LocalComponent($componentName, $exe, $argumentText, $directory) {
    $previous = $existing | Where-Object { $_.name -eq $componentName } | Select-Object -First 1
    if ($previous -and $previous.pid) {
        $active = Get-Process -Id $previous.pid -ErrorAction SilentlyContinue
        if ($active -and $active.StartTime.ToUniversalTime().Ticks -eq ([DateTimeOffset]$previous.started).UtcDateTime.Ticks) {
            $records.Add($previous)
            Save-LocalState
            Write-Output "$componentName already running ($($previous.pid))"
            return
        }
    }
    if ($componentName -ne 'worker' -and (Test-LocalEndpoint $componentName)) {
        $records.Add([PSCustomObject]@{name=$componentName;external=$true})
        Save-LocalState
        Write-Output "$componentName already available (existing local service; not owned by this script)"
        return
    }
    $process = Start-Process -FilePath $exe -ArgumentList $argumentText -WorkingDirectory $directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runDir "$componentName.out.log") -RedirectStandardError (Join-Path $runDir "$componentName.err.log")
    $records.Add([PSCustomObject]@{name=$componentName;pid=$process.Id;started=$process.StartTime.ToUniversalTime().ToString('o')})
    Save-LocalState
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) {
            $reason = Get-Content -Raw -LiteralPath (Join-Path $runDir "$componentName.err.log") -ErrorAction SilentlyContinue
            throw "$componentName failed to start. $reason"
        }
        if ($componentName -eq 'worker') {
            $ready = (Get-Content -Raw -LiteralPath (Join-Path $runDir 'worker.out.log') -ErrorAction SilentlyContinue) -match 'Wenshu worker ready'
        } else { $ready = Test-LocalEndpoint $componentName }
        if ($ready) {
            Write-Output "$componentName ready ($($process.Id))"
            return
        }
    }
    throw "$componentName did not become ready. Inspect $runDir"
}
Start-LocalComponent 'api' $pythonPath '-m uvicorn server.app:app --host 127.0.0.1 --port 8787' $projectRoot
Start-LocalComponent 'worker' $pythonPath '-m server.worker' $projectRoot
Start-LocalComponent 'frontend' $nodePath 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173 --strictPort' $FrontendPath
Save-LocalState
Write-Output 'Preview: http://127.0.0.1:4173'
Write-Output 'API: http://127.0.0.1:8787/api/health'
Write-Output "Data: $env:WENSHU_DATA_DIR"
