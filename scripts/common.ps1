# Shared helpers for the root-level .ps1 commands.
$Root = Split-Path -Parent $PSScriptRoot

function Get-ProjectPython {
    $venv = Join-Path $Root "tools\sharp-venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "Python not found. Run .\setup.ps1 first."
}
