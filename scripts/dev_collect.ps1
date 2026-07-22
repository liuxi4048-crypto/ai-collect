<#
.SYNOPSIS
    Mechanical stages of a dev-collect run. Claude does the translation between.

.DESCRIPTION
        collect  ->  Claude writes pending_dev_notes.json  ->  publish

    Sibling of ai_collect.ps1. Same two-stage shape, but this routine gathers
    build-relevant material (framework releases, changelogs, cloud/infra,
    databases, security advisories) WITHOUT ai-collect's AI-relevance gate, and
    publishes to `12_Dev Archive/` instead of `11_AI Archive/`. The two never
    share state. Deliberately ASCII-only: Windows PowerShell 5.1 reads a
    BOM-less script as ANSI and mangles multibyte string literals.

.PARAMETER Stage
    'collect' gathers and ranks clusters into data/pending_dev_items.json.
    'publish' renders notes into the vault, then commits.

.PARAMETER Limit
    Max clusters to select (collect stage). Default 60.

.PARAMETER TierA
    How many top-ranked clusters get the detailed treatment. Default 12.

.PARAMETER NoPush
    Commit the vault but do not push.

.PARAMETER NoCommit
    Write notes but leave them unstaged. Implies NoPush.

.EXAMPLE
    .\dev_collect.ps1 -Stage collect -Limit 5
    .\dev_collect.ps1 -Stage publish -NoPush
#>
param(
    [Parameter(Mandatory)][ValidateSet('collect', 'publish')][string]$Stage,
    [int]$Limit = 60,
    [int]$TierA = 12,
    [switch]$NoPush,
    [switch]$NoCommit
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) { Write-Host "[dev-collect] $msg" }

if ($Stage -eq 'collect') {
    Write-Step "Collecting (limit=$Limit, tierA=$TierA)"
    python (Join-Path $scriptDir "dev_collect.py") --limit $Limit --tier-a $TierA
    if ($LASTEXITCODE -ne 0) { throw "collect failed" }
    exit 0
}

# --- publish -----------------------------------------------------------------
$publishArgs = @((Join-Path $scriptDir "dev_publish.py"))
if ($NoPush)   { $publishArgs += "--no-push" }
if ($NoCommit) { $publishArgs += "--no-commit" }

Write-Step "Publishing to vault"
python @publishArgs
if ($LASTEXITCODE -ne 0) { throw "publish failed" }
