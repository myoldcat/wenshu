param([string]$InputPath, [string]$OutputDir, [int]$WatchPid = 0, [long]$StartTicks = 0)
$ErrorActionPreference = 'Stop'

if ($WatchPid) {
    # Watch only the new, empty Word instance owned by this synthetic check.
    # A prompt is dismissed with No; never enable external content or alter
    # Word's saved security options.
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $warnings = @()
    $seen = @{}
    $expired = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $WatchPid)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath "$OutputDir/done") { break }
        $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children, $condition)
        foreach ($window in $windows) {
            try {
                $textCondition = [System.Windows.Automation.PropertyCondition]::new(
                    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                    [System.Windows.Automation.ControlType]::Text)
                $texts = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCondition)
                $message = ($texts | ForEach-Object { $_.Current.Name }) -join ' '
                if ($message -notmatch '(?i)(包含.*域.*文件|contains fields.*(files|websites))') { continue }
                $key = $window.Current.NativeWindowHandle.ToString()
                if ($seen.ContainsKey($key)) { continue }
                $seen[$key] = $true
                $warnings += $message
                $buttonCondition = [System.Windows.Automation.PropertyCondition]::new(
                    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                    [System.Windows.Automation.ControlType]::Button)
                $buttons = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
                $decline = $buttons | Where-Object { $_.Current.Name -match '^(否|No\b)' } | Select-Object -First 1
                if ($null -ne $decline) {
                    $pattern = $decline.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                    $pattern.Invoke()
                }
            } catch { }
        }
        Start-Sleep -Milliseconds 100
    }
    if (!(Test-Path -LiteralPath "$OutputDir/done")) {
        $expired = $true
        $process = Get-Process -Id $WatchPid -ErrorAction SilentlyContinue
        if ($null -ne $process -and $process.ProcessName -eq 'WINWORD' -and $process.StartTime.Ticks -eq $StartTicks) {
            Stop-Process -Id $WatchPid -Force
        }
    }
    @{fieldPrompts=$warnings; promptCount=$warnings.Count; timedOut=$expired} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$OutputDir/prompt-check.json" -Encoding utf8
    exit
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
if (Test-Path -LiteralPath "$OutputDir/done") { throw 'Use a new output directory for each check.' }
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WordOpenWindowPid {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
}
'@
$word = $null
$document = $null
$watcher = $null
$ownsInstance = $false
try {
    $word = New-Object -ComObject Word.Application
    if ($word.Documents.Count -ne 0) { throw 'Refusing to test against a Word instance with open user documents.' }
    $ownsInstance = $true
    $word.Visible = $false
    $word.DisplayAlerts = -1
    $word.AutomationSecurity = 3
    $probe = $word.Documents.Add()
    $windowHandle = $probe.Windows.Item(1).Hwnd
    [uint32]$wordPid = 0
    [WordOpenWindowPid]::GetWindowThreadProcessId([IntPtr]$windowHandle, [ref]$wordPid) | Out-Null
    $probe.Close(0)
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($probe) | Out-Null
    $wordStartTicks = (Get-Process -Id $wordPid).StartTime.Ticks
    $pwsh = (Get-Process -Id $PID).Path
    $watcher = Start-Process -FilePath $pwsh -WindowStyle Hidden -PassThru -ArgumentList @(
        '-NoProfile', '-File', $PSCommandPath, '-WatchPid', $wordPid,
        '-StartTicks', $wordStartTicks, '-OutputDir', $OutputDir)
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $document = $word.Documents.Open((Get-Item -LiteralPath $InputPath).FullName, $false, $true)
    $openMilliseconds = $timer.ElapsedMilliseconds
    # Normal layout only: deliberately do not call Fields.Update().
    $document.Repaginate()
    $pages = $document.ComputeStatistics(2)
    $document.ExportAsFixedFormat((Join-Path $OutputDir 'manuscript.pdf'), 17)
    @{renderer='Microsoft Word'; alerts='all'; fieldsUpdateCalled=$false;
      opened=$true; openMilliseconds=$openMilliseconds; pages=$pages} |
        ConvertTo-Json | Set-Content -LiteralPath "$OutputDir/open-check.json" -Encoding utf8
} finally {
    if ($ownsInstance) {
        if ($null -ne $document) { try { $document.Close(0) } catch { } }
        if ($null -ne $word) { try { $word.Quit(0) } catch { } }
    }
    if ($null -ne $document) { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) | Out-Null }
    if ($null -ne $word) { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null }
    [System.IO.File]::WriteAllText((Join-Path $OutputDir 'done'), 'complete')
    if ($null -ne $watcher) { $watcher.WaitForExit(5000) | Out-Null }
}
Get-Content -LiteralPath "$OutputDir/open-check.json"
Get-Content -LiteralPath "$OutputDir/prompt-check.json"
