<#
.SYNOPSIS
  Open a reconstructed scene in the local SuperSplat editor (keyframes + MP4 render).

.EXAMPLE
  .\view.ps1 grotto_photos                               # scene + original camera positions
  .\view.ps1 grotto_photos -Path camera\my_shot.json     # scene + a saved camera path
  .\view.ps1 grotto_photos -NoPoses                      # scene only
  .\view.ps1                                             # empty editor (drag & drop any .ply)
#>
param([string]$Scene, [string]$Path, [switch]$NoPoses, [int]$Port = 8642)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\scripts\common.ps1"
$py = Get-ProjectPython
$pyArgs = @((Join-Path $Root "scripts\serve.py"), "--port", $Port)
if ($Scene) { $pyArgs += $Scene }
if ($Path) { $pyArgs += @("--path", (Resolve-Path $Path).Path) }
if ($NoPoses) { $pyArgs += "--no-poses" }
& $py @pyArgs
