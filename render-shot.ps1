<#
.SYNOPSIS
  Render a camera move through a scene to MP4 without opening the editor
  (uses SuperSplat's renderer in a hidden Edge/Chrome window). Output goes to renders\.

.EXAMPLE
  .\render-shot.ps1 grotto_photos -ListViews                         # list original photo positions
  .\render-shot.ps1 grotto_photos -Shot dolly-right -View 8          # sideways track at photo 8
  .\render-shot.ps1 grotto_photos -Shot push-in -View 3 -Seconds 6 -Amount 0.5
  .\render-shot.ps1 grotto_photos -Shot orbit-left -View 16 -Width 1920 -Height 1080
  .\render-shot.ps1 grotto_photos -Path camera\my_shot.json          # a path saved from the editor
  .\render-shot.ps1 grotto_photos -Shot dolly-left -View 8 -SavePath camera\dolly8.json
      # ...then tweak it interactively:  .\view.ps1 grotto_photos -Path camera\dolly8.json

.NOTES
  Shots: dolly-left, dolly-right, push-in, pull-out, orbit-left, orbit-right,
         crane-up, crane-down, flythrough
  -Amount = size of the move relative to the distance to the subject (0.3 = 30%).
  Keep moves small for few-photo scenes: unseen areas have holes.
#>
param(
    [Parameter(Mandatory = $true)][string]$Scene,
    [ValidateSet("still", "dolly-left","dolly-right", "push-in", "pull-out", "orbit-left", "orbit-right",
                 "crane-up", "crane-down", "flythrough")][string]$Shot,
    [string]$Path,
    [int]$View = 0,
    [double]$Amount = 0.3,
    [double]$Seconds = 4,
    [int]$Fps = 30,
    [int]$Width = 1280,
    [int]$Height = 720,
    [double]$Fov = 0,
    [string]$Out,
    [string]$SavePath,
    [switch]$ListViews,
    [switch]$Show
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\scripts\common.ps1"
$py = Get-ProjectPython
$a = @((Join-Path $Root "scripts\render_path.py"), $Scene, "--view", $View, "--amount", $Amount,
       "--seconds", $Seconds, "--fps", $Fps, "--width", $Width, "--height", $Height)
if ($Shot) { $a += @("--shot", $Shot) }
if ($Path) { $a += @("--path", (Resolve-Path $Path).Path) }
if ($Fov -gt 0) { $a += @("--fov", $Fov) }
if ($Out) { $a += @("--out", $Out) }
if ($SavePath) { $a += @("--save-path", $SavePath) }
if ($ListViews) { $a += "--list-views" }
if ($Show) { $a += "--show" }
& $py @a
