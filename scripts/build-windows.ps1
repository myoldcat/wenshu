param(
    [string]$Version = '0.2.1',
    [string]$PythonPath = '',
    [string]$NodePath = '',
    [string]$IsccPath = '',
    [string]$DistPath = '',
    [switch]$SkipDependencies,
    [switch]$SkipFrontend,
    [switch]$SkipFreeze
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$build = Join-Path $root 'output\windows-build'
$tools = Join-Path $root 'output\windows-tools'
$release = Join-Path $root 'output\windows-release'
$dist = if ($DistPath) { [System.IO.Path]::GetFullPath($DistPath) } else { Join-Path $build 'dist' }
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'output')) + [System.IO.Path]::DirectorySeparatorChar
if (!$dist.StartsWith($outputRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'DistPath must stay inside this project output directory.' }
$bundle = Join-Path $dist 'Wenshu'
$resources = Join-Path $root 'deploy\windows'
$installerStage = Join-Path $build ('installer-stage-' + (Split-Path -Leaf $dist))
if ($Version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') { throw 'Version must contain three or four numeric components.' }
foreach ($directory in @($build, $tools, $release, $installerStage)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$temporary = Join-Path $build 'tmp'
New-Item -ItemType Directory -Path $temporary -Force | Out-Null
$env:TEMP = $temporary
$env:TMP = $temporary
$env:TMPDIR = $temporary
$manifest = Get-Content -Raw -LiteralPath (Join-Path $resources 'tools.json') | ConvertFrom-Json

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Build command failed ($LASTEXITCODE): $File" }
}

function Get-VerifiedDownload([string]$Url, [string]$Destination, [string]$Sha256, [string]$Signer = '') {
    if (!(Test-Path -LiteralPath $Destination)) {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -TimeoutSec 120
    }
    $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
    if ($actual -ne $Sha256) { throw "Download SHA-256 mismatch: $Destination. Review the official release before updating tools.json." }
    if ($Signer) {
        $signature = Get-AuthenticodeSignature -LiteralPath $Destination
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike "*$Signer*") {
            throw "Authenticode verification failed: $Destination"
        }
    }
}

function Get-ReleaseInputHashes {
    $inputs = @()
    $inputs += Get-ChildItem -LiteralPath (Join-Path $root 'desktop') -File -Filter '*.py'
    $inputs += Get-ChildItem -LiteralPath (Join-Path $root 'server') -File -Filter '*.py'
    $inputs += Get-ChildItem -LiteralPath (Join-Path $root 'frontend\dist\client') -Recurse -File
    $inputs += Get-ChildItem -LiteralPath (Join-Path $root 'resources\presets') -Recurse -File
    $inputs += Get-ChildItem -LiteralPath $resources -File
    $inputs += Get-Item -LiteralPath (Join-Path $PSScriptRoot 'build-windows.ps1')
    $inputs += Get-Item -LiteralPath (Join-Path $PSScriptRoot 'prepare-windows-native.py')
    $result = [ordered]@{}
    foreach ($item in ($inputs | Sort-Object FullName)) {
        $relative = $item.FullName.Substring($root.Length + 1).Replace('\', '/')
        $result[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $result
}

if (!$PythonPath) {
    $PythonPath = Join-Path $root 'server\.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $PythonPath)) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if (!$launcher) { throw 'Pass -PythonPath pointing to a Windows x64 Python 3.12 executable.' }
        $PythonPath = (& $launcher.Source -3.12 -c 'import sys; print(sys.executable)').Trim()
    }
}
$architecture = & $PythonPath -c 'import struct,sys; print(str(sys.version_info.major)+"."+str(sys.version_info.minor)+"/"+str(struct.calcsize("P")*8))'
if ($architecture.Trim() -ne '3.12/64') { throw 'This release requires Windows x64 Python 3.12 for reproducible packaging.' }
$python = Join-Path $build '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { Invoke-Checked $PythonPath @('-m', 'venv', (Join-Path $build '.venv')) }
$env:PIP_CACHE_DIR = Join-Path $build 'pip-cache'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONUTF8 = '1'
if (!$SkipDependencies) {
    Invoke-Checked $python @('-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple', '-r', (Join-Path $resources 'requirements.lock'))
}
Invoke-Checked $python @('-m', 'pip', 'check')

if (!$SkipFrontend) {
    if (!$NodePath) { $NodePath = (Get-Command node.exe -ErrorAction Stop).Source }
    $frontend = Join-Path $root 'frontend'
    $vite = Join-Path $frontend 'node_modules\vite\bin\vite.js'
    $nodeDirectory = Split-Path -Parent $NodePath
    $npmCandidates = @((Join-Path $nodeDirectory 'node_modules\npm\bin\npm-cli.js'),
                       (Join-Path (Split-Path -Parent $nodeDirectory) 'node_modules\npm\bin\npm-cli.js'),
                       (Join-Path $tools 'npm-10.9.2\package\bin\npm-cli.js'))
    $npm = $npmCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (!$npm) {
        $npmArchive = Join-Path $tools 'npm-10.9.2.tgz'
        Get-VerifiedDownload $manifest.npm.url $npmArchive $manifest.npm.sha256
        $npmDirectory = Join-Path $tools 'npm-10.9.2'
        New-Item -ItemType Directory -Path $npmDirectory -Force | Out-Null
        Invoke-Checked 'tar.exe' @('-xzf', $npmArchive, '-C', $npmDirectory)
        $npm = Join-Path $npmDirectory 'package\bin\npm-cli.js'
    }
    $env:NPM_CONFIG_CACHE = Join-Path $build 'npm-cache'
    $env:NPM_CONFIG_UPDATE_NOTIFIER = 'false'
    if (!(Test-Path -LiteralPath $vite)) {
        Push-Location $frontend
        try { Invoke-Checked $NodePath @($npm, 'ci') } finally { Pop-Location }
    }
    Push-Location $frontend
    try { Invoke-Checked $NodePath @($npm, 'run', 'build') } finally { Pop-Location }
}

# All native DLLs are prepared from a pinned official WeasyPrint distribution.
Push-Location $root
try { Invoke-Checked $python @('scripts/prepare-windows-native.py') } finally { Pop-Location }
$bootstrapper = Join-Path $tools 'MicrosoftEdgeWebview2Setup.exe'
Get-VerifiedDownload $manifest.webview2.url $bootstrapper $manifest.webview2.sha256 'Microsoft Corporation'

if (!$IsccPath) {
    $IsccPath = Join-Path $tools 'inno-6.0.5\app\ISCC.exe'
    if (!(Test-Path -LiteralPath $IsccPath)) {
        $compiler = Join-Path $tools 'innosetup-6.0.5.exe'
        $extractorZip = Join-Path $tools 'innoextract-1.9-windows.zip'
        Get-VerifiedDownload $manifest.compiler.url $compiler $manifest.compiler.sha256 'Martijn Laan'
        Get-VerifiedDownload $manifest.extractor.url $extractorZip $manifest.extractor.sha256
        $extractorDirectory = Join-Path $tools 'innoextract-1.9'
        Expand-Archive -LiteralPath $extractorZip -DestinationPath $extractorDirectory -Force
        Invoke-Checked (Join-Path $extractorDirectory 'innoextract.exe') @('--output-dir', (Join-Path $tools 'inno-6.0.5'), $compiler)
    }
}

$inputHashes = Get-ReleaseInputHashes
if (!$SkipFreeze) {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $build 'pyinstaller-cache'
    $env:WEASYPRINT_DLL_DIRECTORIES = Join-Path $root 'output\windows-native'
    $env:WENSHU_DESKTOP_CONTROL_DIR = Join-Path $build 'native-hook-check'
    $env:PATH = "$($env:WEASYPRINT_DLL_DIRECTORIES);$env:PATH"
    Push-Location $root
    try {
        $nativeConfigJson = & $python -c 'import json; from desktop.runtime import configure_runtime; print(json.dumps(configure_runtime()))'
        if ($LASTEXITCODE -ne 0) { throw 'Native PDF build-hook configuration failed.' }
        $nativeConfig = $nativeConfigJson | ConvertFrom-Json
        $env:FONTCONFIG_FILE = $nativeConfig.fontconfig
        $env:FONTCONFIG_PATH = $nativeConfig.cache
        $env:XDG_CACHE_HOME = $nativeConfig.cache
        Invoke-Checked $python @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', $dist,
                                '--workpath', (Join-Path $build 'freeze'), (Join-Path $resources 'wenshu.spec'))
    } finally { Pop-Location }
}
# CLR reads the actual process EXE's adjacent config at startup. Keeping it
# outside _internal is required, and this setting affects only this GUI.
Copy-Item -LiteralPath (Join-Path $resources 'Wenshu.exe.config') -Destination (Join-Path $bundle 'Wenshu.exe.config') -Force
foreach ($name in @('Wenshu.exe', 'WenshuService.exe', '_internal\frontend\index.html', '_internal\frontend\operations.html')) {
    if (!(Test-Path -LiteralPath (Join-Path $bundle $name))) { throw "Release input missing: $name" }
}
$unsafeFiles = Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object {
    $_.Name -in @('library.sqlite3', 'library.sqlite3-wal', 'model.key', '.env', 'id_rsa', 'id_ed25519') -or
    $_.Extension -in @('.sqlite3', '.map') -or
    $_.FullName -match '[\\/](node_modules|\.secrets|knowledge_base)[\\/]'
}
if ($unsafeFiles) { throw 'Release audit found forbidden development or user-data files.' }

# Inno 6 Unicode accepts a UTF-8 BOM reliably. Compile a temporary copy while
# keeping all resource paths explicit, without changing source encodings.
$installerSource = Join-Path $build 'installer.iss'
[System.IO.File]::WriteAllText($installerSource, (Get-Content -Raw -LiteralPath (Join-Path $resources 'installer.iss')), [System.Text.UTF8Encoding]::new($true))
Invoke-Checked $IsccPath @("/DBundleDir=$bundle", "/DBootstrapper=$bootstrapper", "/DOutputDir=$installerStage",
                          "/DResourceDir=$resources", "/DAppVersion=$Version", $installerSource)
$installer = Join-Path $installerStage "Wenshu-Setup-$Version-x64.exe"
$sha = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if (!$SkipFreeze) {
    $afterHashes = Get-ReleaseInputHashes
    if (($inputHashes | ConvertTo-Json -Compress) -ne ($afterHashes | ConvertTo-Json -Compress)) {
        throw 'Release source changed while building; rebuild before delivering the installer.'
    }
}
# A rejected build must not replace the previous verified installer. Promote
# only after all input hashes have been checked, on the same output volume.
$deliveredInstaller = Join-Path $release "Wenshu-Setup-$Version-x64.exe"
if (Test-Path -LiteralPath $deliveredInstaller) {
    $previousInstaller = Join-Path $installerStage 'previous-installer.exe'
    [System.IO.File]::Replace($installer, $deliveredInstaller, $previousInstaller)
} else {
    [System.IO.File]::Move($installer, $deliveredInstaller)
}
$installer = $deliveredInstaller
$file = Get-Item -LiteralPath $installer
$record = [ordered]@{
    product = 'Wenshu'; version = $Version; target = 'win-x64'; renderer = 'pywebview / WebView2 Evergreen'
    installer = $file.Name; bytes = $file.Length; sha256 = $sha
    builtAtUtc = [DateTime]::UtcNow.ToString('o'); unsignedPreview = $true
    userDataIncluded = $false; modelWeightsIncluded = $false
    dependencyLockSha256 = (Get-FileHash (Join-Path $resources 'requirements.lock') -Algorithm SHA256).Hash.ToLowerInvariant()
    bootstrapperSha256 = $manifest.webview2.sha256
    compiler = $IsccPath
    applicationDirectory = $bundle.Substring($root.Length + 1).Replace('\', '/')
    freezeSkipped = [bool]$SkipFreeze
    inputHashes = $inputHashes
}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $release 'BUILD-MANIFEST.json') -Encoding utf8
"$sha  $($file.Name)" | Set-Content -LiteralPath (Join-Path $release 'SHA256SUMS.txt') -Encoding ascii
Write-Output "Installer: $installer"
Write-Output "Size: $($file.Length) bytes"
Write-Output "SHA256: $sha"
