<#
.SYNOPSIS
  ONE photo -> AI-inferred 3D splat (Apple SHARP). Small camera moves only.

.EXAMPLE
  .\single-photo.ps1 "input\my photo.jpg"
  .\single-photo.ps1 media\a.jpg media\b.jpg -Device cpu
#>
param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Images,
    [ValidateSet("auto", "cpu", "cuda")][string]$Device = "auto"
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\scripts\common.ps1"
$py = Get-ProjectPython
& $py (Join-Path $Root "scripts\single_photo.py") @Images --device $Device
