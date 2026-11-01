$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".build-venv"
$python = Join-Path $venv "Scripts\python.exe"
$version = "0.4.11"
$releaseInjectorName = "WindowTintInjector-0.4.11.dll"
$stagingDir = Join-Path $root "build\release-injector"
$releaseInjector = Join-Path $stagingDir $releaseInjectorName

if (-not (Test-Path $python)) {
    python -m venv $venv
}

& $python -m pip install --disable-pip-version-check -r (Join-Path $root "requirements-build.txt")
& $python (Join-Path $root "tools\make_icon.py")
New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
& (Join-Path $root "build_injector.ps1") `
    -OutputName $releaseInjectorName `
    -OutputDir $stagingDir

$arguments = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "WindowTint",
    "--icon", (Join-Path $root "assets\windowtint.ico"),
    "--manifest", (Join-Path $root "app.manifest"),
    "--add-data", "$(Join-Path $root 'assets\windowtint.ico');assets",
    "--add-data", "$(Join-Path $root 'assets\windowtint.png');assets",
    "--add-binary", "$releaseInjector;bin",
    "--paths", (Join-Path $root "src"),
    (Join-Path $root "run.py")
)

Push-Location $root
try {
    & $python -m PyInstaller @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $exe = Join-Path $root "dist\WindowTint.exe"
    $exeHash = Get-FileHash -Algorithm SHA256 $exe
    $releaseDir = Join-Path $root "dist\WindowTint-$version-win64"
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    Copy-Item -LiteralPath $exe -Destination $releaseDir -Force
    Copy-Item -LiteralPath $releaseInjector -Destination $releaseDir -Force
    Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $releaseDir -Force
    Copy-Item -LiteralPath (Join-Path $root "COMPLIANCE.md") -Destination $releaseDir -Force
    Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination $releaseDir -Force
    Copy-Item -LiteralPath (Join-Path $root "THIRD_PARTY_NOTICES.md") -Destination $releaseDir -Force
    $releaseDocsDir = Join-Path $releaseDir "docs"
    New-Item -ItemType Directory -Force -Path $releaseDocsDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $root "docs\ui-preview.png") -Destination $releaseDocsDir -Force
    $licenseDir = Join-Path $releaseDir "licenses"
    New-Item -ItemType Directory -Force -Path $licenseDir | Out-Null
    $pystrayLicense = Join-Path $venv "Lib\site-packages\pystray-0.19.5.dist-info\COPYING.LGPL"
    if (Test-Path -LiteralPath $pystrayLicense) {
        Copy-Item -LiteralPath $pystrayLicense -Destination (Join-Path $licenseDir "pystray-LGPL.txt") -Force
    }
    $checksumFile = Join-Path $releaseDir "SHA256SUMS.txt"
    Set-Content -LiteralPath $checksumFile -Encoding ascii -Value @(
        "$($exeHash.Hash)  WindowTint.exe",
        "$((Get-FileHash -Algorithm SHA256 $releaseInjector).Hash)  WindowTintInjector-0.4.11.dll"
    )
    $zip = Join-Path $root "dist\WindowTint-$version-win64.zip"
    Compress-Archive -Path (Join-Path $releaseDir "*") -DestinationPath $zip -Force
    Remove-Item -LiteralPath $exe -Force
    "Built: $exe"
    "EXE SHA256: $($exeHash.Hash)"
    "Release: $zip"
}
finally {
    Pop-Location
}
