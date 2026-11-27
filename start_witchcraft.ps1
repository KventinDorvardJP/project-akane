param(
    [string]$WitchcraftPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'witchcraft')
)

$ErrorActionPreference = 'Stop'
$akanePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$akaneAdapter = Join-Path $PSScriptRoot 'witchcraft_terminal.py'
$akaneEndpoint = Join-Path $PSScriptRoot 'runtime\witchcraft_endpoint.json'
$witchcraftDaemon = Join-Path $WitchcraftPath 'tools\witchcraft-daemon'
$witchcraftCli = Join-Path $witchcraftDaemon 'bin\witchcraft.js'
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$witchcraftNode = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $env:ProgramFiles 'nodejs\node.exe' }
foreach ($requiredPath in @($akanePython, $akaneAdapter, $witchcraftCli, $witchcraftNode)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file not found: $requiredPath"
    }
}
Write-Host 'Keep Akane running with: .\run.ps1 -Gui -Witchcraft (enable speech recognition for hold-to-talk)'
Write-Host 'The CODEX tab will connect to Akane. No coding agent or command shell is launched.'
Push-Location -LiteralPath $witchcraftDaemon
try {
    & $witchcraftNode $witchcraftCli run --only codex --no-current --no-mcp --no-overlay --akane-endpoint $akaneEndpoint -- $akanePython -X utf8 -u $akaneAdapter
    $witchcraftExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $witchcraftExitCode
