param([string]$AddOnsPath = 'D:\Games\World of Warcraft\_forever_\Interface\AddOns')
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'wow_addon\AkaneData'
$files = @('AkaneData.toc', 'Protocol.lua', 'Collector.lua', 'AkaneData.lua')
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $file) -PathType Leaf)) { throw "Missing addon source: $file" }
}
$addonRoot = [IO.Path]::GetFullPath($AddOnsPath).TrimEnd('\')
if (-not (Test-Path -LiteralPath $addonRoot -PathType Container) -or (Split-Path $addonRoot -Leaf) -ne 'AddOns') {
    throw 'Choose the existing WoW Interface\AddOns directory.'
}
$target = [IO.Path]::GetFullPath((Join-Path $addonRoot 'AkaneData'))
if ((Split-Path $target -Parent) -ne $addonRoot -or (Split-Path $target -Leaf) -ne 'AkaneData') {
    throw 'Addon destination escaped the selected AddOns directory.'
}
$marker = Join-Path $target '.akane-owned'
if (Test-Path -LiteralPath $target) {
    if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'AkaneData must be a real folder, not a junction or symbolic link.' }
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'An unmarked AkaneData folder already exists; it was not overwritten.' }
    $backup = Join-Path $PSScriptRoot ('runtime\wow_addon_backups\' + (Get-Date -Format 'yyyyMMddTHHmmssfff'))
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Get-ChildItem -LiteralPath $target -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $backup }
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $source $file) -Destination $target -Force }
Set-Content -LiteralPath $marker -Value 'Project Akane managed addon' -Encoding utf8
Write-Host "Installed AkaneData to $target"
Write-Host 'Fully restart WoW to discover the new addon, enable Akane Data, then start Akane with -Wow.'
