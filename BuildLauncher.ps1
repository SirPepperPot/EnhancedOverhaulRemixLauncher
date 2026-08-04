param(
    [string]$PythonExe = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$repositoryRoot = $PSScriptRoot
$sourceRoot = Join-Path $repositoryRoot "src"
$testsRoot = Join-Path $repositoryRoot "tests"
$assetRoot = Join-Path $repositoryRoot "assets"
$sevenZipRoot = Join-Path $repositoryRoot "third_party\7zip"
$versionPath = Join-Path $repositoryRoot "VERSION"
$workRoot = Join-Path $repositoryRoot "build"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repositoryRoot "dist"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $PythonExe = $venvPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $PythonExe = $pythonCommand.Source
        }
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe) -or
    -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python was not found. Create .venv as described in README.md or pass -PythonExe."
}

foreach ($requiredFile in @(
    (Join-Path $sourceRoot "launcher_app.py"),
    (Join-Path $sourceRoot "launcher_core.py"),
    (Join-Path $assetRoot "launcher_icon.png"),
    (Join-Path $assetRoot "launcher_icon.ico"),
    (Join-Path $sevenZipRoot "7z.exe"),
    (Join-Path $sevenZipRoot "7z.dll"),
    $versionPath
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required build file is missing: $requiredFile"
    }
}

$launcherVersion = (Get-Content -LiteralPath $versionPath -Raw).Trim()
if ($launcherVersion -notmatch '^\d+(\.\d+){1,3}$') {
    throw "VERSION must contain a numeric dotted version."
}

if (Test-Path -LiteralPath $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $workRoot,$OutputDir -Force | Out-Null

$metadataPath = Join-Path $workRoot "build_metadata.json"
@{
    launcher_version = $launcherVersion
    build_id = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
} | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding UTF8

$versionParts = @($launcherVersion.Split('.'))
while ($versionParts.Count -lt 4) {
    $versionParts += "0"
}
$fileVersion = $versionParts[0..3] -join ", "
$versionResource = Join-Path $workRoot "version_info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($fileVersion),
    prodvers=($fileVersion),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'SirPepperPot'),
          StringStruct(u'FileDescription', u'Enhanced Overhaul Revamped Mod Launcher'),
          StringStruct(u'FileVersion', u'$launcherVersion'),
          StringStruct(u'InternalName', u'EnhancedOverhaulLauncher'),
          StringStruct(u'OriginalFilename', u'EnhancedOverhaulLauncher.exe'),
          StringStruct(u'ProductName', u'Enhanced Overhaul Launcher'),
          StringStruct(u'ProductVersion', u'$launcherVersion')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -LiteralPath $versionResource -Encoding UTF8

$env:PYTHONPATH = $sourceRoot
& $PythonExe -m unittest discover -s $testsRoot -p "test_launcher.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Launcher tests failed."
}

$pyInstallerWork = Join-Path $workRoot "pyinstaller"
$specRoot = Join-Path $workRoot "spec"
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --noupx `
    --exclude-module kivy `
    --exclude-module kivymd `
    --name EnhancedOverhaulLauncher `
    --icon (Join-Path $assetRoot "launcher_icon.ico") `
    --version-file $versionResource `
    --distpath $OutputDir `
    --workpath $pyInstallerWork `
    --specpath $specRoot `
    --paths $sourceRoot `
    --add-data "$assetRoot;assets" `
    --add-data "$sevenZipRoot;tools" `
    --add-data "$metadataPath;." `
    (Join-Path $sourceRoot "launcher_app.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller launcher build failed."
}

$builtExe = Join-Path $OutputDir "EnhancedOverhaulLauncher.exe"
$smokeLog = Join-Path ([System.IO.Path]::GetTempPath()) "EORLauncherSmoke.log"
if (Test-Path -LiteralPath $smokeLog) {
    Remove-Item -LiteralPath $smokeLog -Force
}

$process = Start-Process `
    -FilePath $builtExe `
    -ArgumentList "--smoke-test" `
    -WindowStyle Hidden `
    -PassThru
if (-not $process.WaitForExit(30000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Compiled launcher smoke test timed out."
}
if ($process.ExitCode -ne 0) {
    if (Test-Path -LiteralPath $smokeLog) {
        Get-Content -LiteralPath $smokeLog | Write-Host
    }
    throw "Compiled launcher smoke test failed with exit code $($process.ExitCode)."
}

$hash = Get-FileHash -LiteralPath $builtExe -Algorithm SHA256
"$($hash.Hash)  EnhancedOverhaulLauncher.exe" | Set-Content `
    -LiteralPath (Join-Path $OutputDir "SHA256SUMS.txt") `
    -Encoding ASCII

Write-Host "Launcher created: $builtExe"
Write-Host "Launcher version: $launcherVersion"
Write-Host "Launcher SHA256: $($hash.Hash)"
