param(
    [switch]$FakeBackend,
    [switch]$NoStt,
    [switch]$NoTts,
    [switch]$Hotkeys,
    [switch]$Gui,
    [switch]$Overlay,
    [ValidateSet('ollama', 'openai')][string]$LlmBackend
)

# Use the project's interpreter directly, so activating the venv is unnecessary.
$akanePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $akanePython)) {
    throw 'Create .venv and install requirements.txt first; see README.md.'
}
$akaneArguments = @(Join-Path $PSScriptRoot 'main.py')
if ($FakeBackend) { $akaneArguments += '--fake-backend' }
if ($NoStt) { $akaneArguments += '--no-stt' }
if ($NoTts) { $akaneArguments += '--no-tts' }
if ($Hotkeys) { $akaneArguments += '--hotkeys' }
if ($Gui) { $akaneArguments += '--gui' }
if ($Overlay) { $akaneArguments += '--overlay' }
if ($LlmBackend) { $akaneArguments += @('--llm-backend', $LlmBackend) }
# Keep multilingual output readable when stdout is redirected as well as in a console.
& $akanePython -X utf8 @akaneArguments
exit $LASTEXITCODE
