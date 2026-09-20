[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command,
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Command)) {
    throw "用法：.\scripts\run.ps1 cut|review|render <视频> [参数]"
}
$CliArgs = @($Command) + @($Rest)

$ScriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillDir = Split-Path -Parent $ScriptsDir
$PythonBin = Join-Path $SkillDir ".venv\Scripts\python.exe"
$EditorScript = Join-Path $ScriptsDir "video_editor.py"
$CredentialProfile = Join-Path $ScriptsDir "credential-ui\src\profile.ts"

if (-not (Test-Path -LiteralPath $PythonBin)) {
    throw "找不到 Windows 虚拟环境，请先运行 .\setup.ps1"
}

$HasHelp = ($CliArgs -contains "--help") -or ($CliArgs -contains "-h")
if ($Command -eq "cut" -and -not $HasHelp) {
    & $PythonBin -c "import sys; sys.path.insert(0, sys.argv[1]); from transcriber import load_api_key; load_api_key()" $ScriptsDir
    if ($LASTEXITCODE -ne 0) {
        if ($null -eq (Get-Command node -ErrorAction SilentlyContinue)) {
            throw "未找到 Node.js，无法打开凭据配置入口。"
        }
        & node $CredentialProfile run default -- $PythonBin $EditorScript @CliArgs
        exit $LASTEXITCODE
    }
}

& $PythonBin $EditorScript @CliArgs
exit $LASTEXITCODE
