param(
    [string]$Version = '0.2.1',
    [string]$BundlePath = '',
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$release = Join-Path $root 'output\windows-release'
if ($Version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') { throw 'Version must have three or four numeric components.' }
$originalManifest = Join-Path $release "BUILD-MANIFEST-$Version.json"
if (!(Test-Path -LiteralPath $originalManifest)) { throw 'Build and verify the matching frozen application first.' }
$frozen = Get-Content -Raw -LiteralPath $originalManifest | ConvertFrom-Json
if ($frozen.version -ne $Version) { throw 'Frozen application version mismatch.' }
if (!$BundlePath) { $BundlePath = Join-Path $root $frozen.applicationDirectory }
$bundle = [System.IO.Path]::GetFullPath($BundlePath)
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'output')) + [System.IO.Path]::DirectorySeparatorChar
if (!$bundle.StartsWith($outputRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'BundlePath must remain inside output/.' }
foreach ($required in @('Wenshu.exe', 'WenshuService.exe', 'Wenshu.exe.config', '_internal\frontend\index.html', '_internal\presets\manifest.json', '_internal\presets\manual\user-guide.html', '_internal\presets\manual\user-guide-server.html', '_internal\presets\manual\user-guide-windows.html')) {
    if (!(Test-Path -LiteralPath (Join-Path $bundle $required))) { throw "Missing frozen application file: $required" }
}
if (!$PythonPath) { $PythonPath = Join-Path $root 'output\windows-build\.venv\Scripts\python.exe' }
$build = Join-Path $root ('output\windows-build\portable-' + [Guid]::NewGuid().ToString('N'))
$stage = Join-Path $build 'Wenshu'
$program = Join-Path $stage 'app'
$tools = Join-Path $stage 'tools'
foreach ($directory in @($program, $tools, (Join-Path $stage 'data'), (Join-Path $stage 'profile'), (Join-Path $build 'temp'))) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$env:TEMP = Join-Path $build 'temp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:PYTHONUTF8 = '1'
# Copy only a previously verified frozen application, never the checkout.
Get-ChildItem -LiteralPath $bundle -Force | Copy-Item -Destination $program -Recurse
$resources = Join-Path $root 'deploy\windows'
$toolInfo = Get-Content -Raw -LiteralPath (Join-Path $resources 'tools.json') | ConvertFrom-Json
$bootstrapper = Join-Path $root 'output\windows-tools\MicrosoftEdgeWebview2Setup.exe'
if ((Get-FileHash -LiteralPath $bootstrapper -Algorithm SHA256).Hash.ToLowerInvariant() -ne $toolInfo.webview2.sha256) { throw 'WebView2 tool SHA-256 mismatch.' }
$signature = Get-AuthenticodeSignature -LiteralPath $bootstrapper
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') { throw 'Invalid Microsoft runtime tool signature.' }
Copy-Item -LiteralPath $bootstrapper -Destination $tools
$assemblyVersion = if ($Version.Split('.').Count -eq 3) { "$Version.0" } else { $Version }
$source = (Get-Content -Raw -LiteralPath (Join-Path $resources 'portable-launcher.cs')).Replace('__APP_VERSION__', $assemblyVersion)
$generated = Join-Path $build 'portable-launcher.cs'
[System.IO.File]::WriteAllText($generated, $source, [System.Text.UTF8Encoding]::new($true))
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 /reference:System.Windows.Forms.dll "/out:$stage\Wenshu.exe" $generated
if ($LASTEXITCODE -ne 0) { throw 'Portable launcher compilation failed.' }
$readme = (Get-Content -Raw -LiteralPath (Join-Path $resources 'portable-readme.txt')).Replace('0.2.1', $Version)
[System.IO.File]::WriteAllText((Join-Path $stage '使用说明.txt'), $readme, [System.Text.UTF8Encoding]::new($true))
$name = "Wenshu-Portable-$Version-x64.zip"
$temporaryArchive = Join-Path $build $name
$packageJson = & $PythonPath (Join-Path $PSScriptRoot 'package-windows-portable.py') $stage $temporaryArchive
if ($LASTEXITCODE -ne 0) { throw 'Portable archive audit or build failed.' }
$package = $packageJson | ConvertFrom-Json
$destination = Join-Path $release $name
if (Test-Path -LiteralPath $destination) {
    [System.IO.File]::Replace($temporaryArchive, $destination, (Join-Path $build 'previous-portable.zip'))
} else { [System.IO.File]::Move($temporaryArchive, $destination) }
$record = [ordered]@{
    product = 'Wenshu'; version = $Version; target = 'win-x64'; format = 'portable-zip'
    artifact = $name; bytes = $package.bytes; expandedBytes = $package.expandedBytes; files = $package.files; sha256 = $package.sha256
    builtAtUtc = [DateTime]::UtcNow.ToString('o'); unsignedPreview = $true
    stageDirectory = $stage; originalFrozenDirectory = $bundle
    originalFrozenManifestSha256 = (Get-FileHash -LiteralPath $originalManifest -Algorithm SHA256).Hash.ToLowerInvariant()
    launcherSourceSha256 = (Get-FileHash -LiteralPath (Join-Path $resources 'portable-launcher.cs') -Algorithm SHA256).Hash.ToLowerInvariant()
    launcherSha256 = (Get-FileHash -LiteralPath (Join-Path $stage 'Wenshu.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
    presetManifestSha256 = (Get-FileHash -LiteralPath (Join-Path $program '_internal\presets\manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    bootstrapperSha256 = $toolInfo.webview2.sha256
    defaultData = 'Wenshu/data'; defaultProfile = 'Wenshu/profile'; defaultTemporaryFiles = 'Wenshu/profile/temp'
    sharedWebView2Required = $true; fixedBrowserIncluded = $false
    installRegistrationRequired = $false; automaticRuntimeInstallation = $false
    userDataIncluded = $false; modelCredentialsIncluded = $false; modelWeightsIncluded = $false
}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $release "BUILD-MANIFEST-Portable-$Version.json") -Encoding utf8
$sumFile = Join-Path $release 'SHA256SUMS.txt'
$sums = if (Test-Path -LiteralPath $sumFile) { @(Get-Content -LiteralPath $sumFile | Where-Object { $_ -and $_ -notmatch ([Regex]::Escape($name) + '$') }) } else { @() }
@($sums; "$($package.sha256)  $name") | Set-Content -LiteralPath $sumFile -Encoding ascii
$record | ConvertTo-Json -Depth 5
