$ErrorActionPreference = "Stop"
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
& pythonw.exe (Join-Path $PSScriptRoot "run.py")
