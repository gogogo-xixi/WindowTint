param(
    [string]$OutputName = "WindowTintInjector.dll",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$source = Join-Path $root "native\injector\WindowTintInjector.cpp"
$definition = Join-Path $root "native\injector\WindowTintInjector.def"
$manifest = Join-Path $root "native\injector\WindowTintInjector.manifest"
$outputDir = if ($OutputDir) {
    if ([IO.Path]::IsPathRooted($OutputDir)) {
        [IO.Path]::GetFullPath($OutputDir)
    } else {
        [IO.Path]::GetFullPath((Join-Path $root $OutputDir))
    }
} else {
    Join-Path $root "bin"
}
$objectDir = Join-Path $root "build\injector"
$output = Join-Path $outputDir $OutputName
$objectName = [IO.Path]::GetFileNameWithoutExtension($OutputName) + ".obj"
$object = Join-Path $objectDir $objectName
$mapName = [IO.Path]::GetFileNameWithoutExtension($OutputName) + ".map"
$map = Join-Path $outputDir $mapName

$vcvarsCandidates = @(
    $env:WINDOWTINT_VCVARS64,
    "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
)
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path -LiteralPath $vswhere) {
    $installations = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    foreach ($installation in $installations) {
        $vcvarsCandidates += Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
    }
}
$vcvars = $vcvarsCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $vcvars) {
    throw "Visual Studio x64 build tools were not found. Set WINDOWTINT_VCVARS64 to vcvars64.bat."
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
New-Item -ItemType Directory -Force -Path $objectDir | Out-Null

$compile = @(
    "call `"$vcvars`" >nul &&",
    "cl.exe /nologo /std:c++17 /EHsc /O2 /MT /DUNICODE /D_UNICODE",
    "/LD `"$source`" /Fo:`"$object`" /Fe:`"$output`"",
    "/link /DEF:`"$definition`" /MAP:`"$map`" user32.lib gdi32.lib gdiplus.lib dwmapi.lib &&",
    "mt.exe -nologo -manifest `"$manifest`" `"-outputresource:$output;#2`""
) -join " "

& cmd.exe /d /s /c $compile
if ($LASTEXITCODE -ne 0) {
    throw "Injector build failed with exit code $LASTEXITCODE"
}

$hash = Get-FileHash -Algorithm SHA256 $output
"Built: $output"
"Map: $map"
"SHA256: $($hash.Hash)"
