param([string]$InputPath = '', [string]$OutputDir = '', [string]$PdfName = 'requirements.pdf')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$inputDoc = if ($InputPath) { Get-Item -LiteralPath $InputPath } else { Get-ChildItem -LiteralPath (Join-Path $taskRoot 'docs-dev') -Filter '*.docx' | Where-Object { $_.Name -notlike 'KooHub*' } | Select-Object -First 1 }
if ($null -eq $inputDoc) { throw 'No DOCX found in docs-dev.' }
$renderDir = if ($OutputDir) { $OutputDir } else { Join-Path $taskRoot '.document-work\render' }
New-Item -ItemType Directory -Path $renderDir -Force | Out-Null
$pdfPath = Join-Path $renderDir $PdfName
$wordApp = $null
$wordDoc = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    $wordDoc = $wordApp.Documents.Open($inputDoc.FullName, $false, $true)
    $wordDoc.Repaginate()
    $wordDoc.Fields.Update() | Out-Null
    $pageCount = $wordDoc.ComputeStatistics(2)
    $wordDoc.ExportAsFixedFormat($pdfPath, 17)
    [PSCustomObject]@{renderer='Microsoft Word'; pages=$pageCount; pdf=$pdfPath} | ConvertTo-Json -Compress
} finally {
    if ($null -ne $wordDoc) {
        $wordDoc.Close(0)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordDoc) | Out-Null
    }
    if ($null -ne $wordApp) {
        $wordApp.Quit(0)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) | Out-Null
    }
}
