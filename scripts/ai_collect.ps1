<#
.SYNOPSIS
    Mechanical stages of an ai-collect run. Claude does the translation between.

.DESCRIPTION
        collect  ->  Claude writes pending_notes.json  ->  publish

    Two allowlistable commands, so an unattended run needs no broad shell
    permission. Deliberately ASCII-only: Windows PowerShell 5.1 reads a
    BOM-less script as ANSI and mangles multibyte string literals.

.PARAMETER Stage
    'collect' gathers and ranks clusters into data/pending_items.json.
    'publish' renders notes into the vault, then commits.

.PARAMETER Limit
    Max clusters to select (collect stage). Default 70.

.PARAMETER TierA
    How many top-ranked clusters get the detailed treatment. Default 15.

.PARAMETER NoPush
    Commit the vault but do not push.

.PARAMETER NoCommit
    Write notes but leave them unstaged. Implies NoPush.

.EXAMPLE
    .\ai_collect.ps1 -Stage collect -Limit 5
    .\ai_collect.ps1 -Stage publish -NoPush
#>
param(
    [Parameter(Mandatory)][ValidateSet('collect', 'publish')][string]$Stage,
    [int]$Limit = 70,
    [int]$TierA = 15,
    [switch]$NoPush,
    [switch]$NoCommit
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) { Write-Host "[ai-collect] $msg" }

if ($Stage -eq 'collect') {
    Write-Step "Collecting (limit=$Limit, tierA=$TierA)"
    python (Join-Path $scriptDir "collect.py") --limit $Limit --tier-a $TierA
    if ($LASTEXITCODE -ne 0) { throw "collect failed" }
    exit 0
}

# --- publish -----------------------------------------------------------------
$publishArgs = @((Join-Path $scriptDir "publish.py"))
if ($NoPush)   { $publishArgs += "--no-push" }
if ($NoCommit) { $publishArgs += "--no-commit" }

Write-Step "Publishing to vault"
python @publishArgs
if ($LASTEXITCODE -ne 0) { throw "publish failed" }
