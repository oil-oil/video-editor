[CmdletBinding()]
param(
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"
$SkillDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $SkillDir

function Get-RequiredCommand([string[]]$Names) {
    foreach ($Name in $Names) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($null -ne $Command) { return $Command.Source }
    }
    throw "找不到 $($Names -join ' 或 ')。请先安装并加入 PATH。"
}

$BootstrapPython = Get-RequiredCommand @("python", "py")
Get-RequiredCommand @("ffmpeg") | Out-Null
Get-RequiredCommand @("ffprobe") | Out-Null

Write-Host "=== video-editor Windows Setup ==="
Write-Host "Skill directory: $SkillDir"

$VenvDir = Join-Path $SkillDir ".venv"
$PythonBin = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonBin)) {
    & $BootstrapPython -m venv $VenvDir
}

if (-not $SkipDependencies) {
    & $PythonBin -m pip install --quiet --upgrade pip
    & $PythonBin -m pip install --quiet silero-vad torch dashscope
    & $PythonBin -c "import silero_vad; print('silero-vad verified.')"
} else {
    Write-Host "Skipping Python dependency installation."
}

if ($null -ne (Get-Command bl -ErrorAction SilentlyContinue)) {
    Write-Host "bl CLI: available"
} else {
    Write-Host "bl CLI: not found; DASHSCOPE_API_KEY or the credential page is required for cut."
}

Write-Host "=== Setup complete ==="
Write-Host "Use: .\scripts\run.ps1 cut <video>"
