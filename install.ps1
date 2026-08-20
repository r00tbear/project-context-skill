# project-context installer for Windows PowerShell 5.1+ / PowerShell 7.
# One command, safe to re-run (re-running updates):
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/r00tbear/project-context-skill/main/install.ps1 | iex"
#
# Mirrors install.sh exactly: checks git and Python 3.11+, resolves the latest release
# tag, installs/updates ~/.agents/skills/project-context, writes the Claude adapter,
# installs and registers jCodeMunch (required), archives any legacy copy into
# ~/.skill-backups (never deletes), runs self-check.
# Overrides: PROJECT_CONTEXT_REPO, PROJECT_CONTEXT_VERSION, PROJECT_CONTEXT_HOME,
# PROJECT_CONTEXT_NO_JCODEMUNCH=1 (skip the jCodeMunch step, CI/hermetic).

$ErrorActionPreference = "Stop"

function Say($Message) { Write-Host "[project-context] $Message" -ForegroundColor Cyan }
# Errors go to the host's error surface, which the console host routes to stderr
# (mirrors install.sh's >&2) and custom hosts (ISE, VS Code) still display.
function Fail($Message) { $Host.UI.WriteErrorLine("[project-context] error: $Message"); exit 1 }
# Under ErrorActionPreference=Stop, Windows PowerShell 5.1 can raise NativeCommandError
# when a native command writes to a redirected stderr - BEFORE any $LASTEXITCODE guard
# runs. Every stderr-silenced native call goes through this scope-limited wrapper.
function Quiet([scriptblock]$Command) {
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command 2>$null } finally { $ErrorActionPreference = $Previous }
}

$RepoUrl = if ($env:PROJECT_CONTEXT_REPO) { $env:PROJECT_CONTEXT_REPO } else { "https://github.com/r00tbear/project-context-skill.git" }
$HomeDir = if ($env:PROJECT_CONTEXT_HOME) { $env:PROJECT_CONTEXT_HOME } else { $HOME }
$Payload = Join-Path $HomeDir ".agents\skills\project-context"
$AdapterDir = Join-Path $HomeDir ".claude\skills\project-context"
$Backups = Join-Path $HomeDir ".skill-backups"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "git is required. Install it first: https://git-scm.com/downloads"
}
$Python = $null
foreach ($Candidate in @("python3", "python", "py")) {
    $Found = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($Found) {
        Quiet { & $Found.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" }
        if ($LASTEXITCODE -eq 0) { $Python = $Found.Source; break }
    }
}
if (-not $Python) { Fail "Python 3.11+ is required. Install it first: https://www.python.org/downloads/" }

$Version = $env:PROJECT_CONTEXT_VERSION
if (-not $Version) {
    $Tags = Quiet { git ls-remote --tags --refs $RepoUrl "v*" }
    if ($LASTEXITCODE -ne 0) { Fail "could not reach $RepoUrl" }
    $Version = $Tags |
        ForEach-Object { ($_ -split "/")[-1] } |
        Where-Object { $_ -match "^v[0-9]+\.[0-9]+\.[0-9]+$" } |
        Sort-Object { [version]$_.Substring(1) } |
        Select-Object -Last 1
}
if (-not $Version) { Fail "could not resolve a release tag from $RepoUrl" }
Say "installing release $Version"

# Archive a legacy full copy living where the small adapter belongs (v0.1/v0.2 layouts).
if ((Test-Path $AdapterDir) -and ((Test-Path (Join-Path $AdapterDir "auditors")) -or (Test-Path (Join-Path $AdapterDir ".git")))) {
    New-Item -ItemType Directory -Force -Path $Backups | Out-Null
    Move-Item $AdapterDir (Join-Path $Backups "claude-project-context-$Stamp")
    Say "archived the old copy from .claude\skills\project-context to .skill-backups\claude-project-context-$Stamp"
}
$LegacyCodex = Join-Path $HomeDir ".codex\skills\project-context"
if (Test-Path $LegacyCodex) {
    New-Item -ItemType Directory -Force -Path $Backups | Out-Null
    Move-Item $LegacyCodex (Join-Path $Backups "codex-project-context-$Stamp")
    Say "archived the old copy from .codex\skills\project-context to .skill-backups\codex-project-context-$Stamp"
}

if (Test-Path (Join-Path $Payload ".git")) {
    $Origin = Quiet { git -C $Payload remote get-url origin }
    if ($Origin -notlike "*project-context-skill*") {
        Fail "$Payload exists but is not a project-context clone (origin: $Origin). Move it aside and re-run."
    }
    Say "updating existing installation"
    git -C $Payload fetch --quiet --tags origin
    if ($LASTEXITCODE -ne 0) { Fail "git fetch failed" }
    git -C $Payload -c advice.detachedHead=false checkout --quiet $Version
    if ($LASTEXITCODE -ne 0) { Fail "git checkout $Version failed" }
} elseif (Test-Path $Payload) {
    Fail "$Payload exists and is not a git clone. Move it aside and re-run."
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $Payload) | Out-Null
    Quiet { git -c advice.detachedHead=false clone --quiet --branch $Version --depth 1 $RepoUrl $Payload }
    if ($LASTEXITCODE -ne 0) {
        git -c advice.detachedHead=false clone --quiet --branch $Version $RepoUrl $Payload
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed" }
    }
}

New-Item -ItemType Directory -Force -Path $AdapterDir | Out-Null
Copy-Item (Join-Path $Payload "templates\host\claude-skill-adapter.md") (Join-Path $AdapterDir "SKILL.md") -Force

# jCodeMunch: the local, offline code index the skill audits through. Required.
if ($env:PROJECT_CONTEXT_NO_JCODEMUNCH -eq "1") {
    Say "skipping jCodeMunch (PROJECT_CONTEXT_NO_JCODEMUNCH=1); the skill requires it at run time"
} else {
    # uv/pipx install into per-user bin directories that may not be on PATH yet.
    # Extend PATH before ANY lookup (mirrors install.sh), honouring PROJECT_CONTEXT_HOME.
    $env:PATH = (Join-Path $HomeDir ".local\bin") + ";$env:LOCALAPPDATA\Programs\Python\Scripts;$env:PATH"
    if (-not (Get-Command jcodemunch-mcp -ErrorAction SilentlyContinue)) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Say "installing jCodeMunch with uv"
            uv tool install --quiet jcodemunch-mcp
            if ($LASTEXITCODE -ne 0) { Fail "uv tool install jcodemunch-mcp failed" }
        } elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
            Say "installing jCodeMunch with pipx"
            pipx install --quiet jcodemunch-mcp
            if ($LASTEXITCODE -ne 0) { Fail "pipx install jcodemunch-mcp failed" }
        } else {
            Fail "jCodeMunch is required and neither uv nor pipx is available. Install uv first (https://docs.astral.sh/uv/): powershell -c `"irm https://astral.sh/uv/install.ps1 | iex`" - then re-run this installer."
        }
    }
    if (-not (Get-Command jcodemunch-mcp -ErrorAction SilentlyContinue)) {
        Fail "jcodemunch-mcp installed but not on PATH; open a new terminal and re-run this installer."
    }
    $JVersion = Quiet { jcodemunch-mcp --version }
    Say "jCodeMunch $JVersion"
    # Registration failures are not fatal: the skill's preflight re-checks and prints
    # the same command, so the user is never stuck silently.
    # Run init from a scratch directory: it drops agent-instruction files (AGENTS.md,
    # .windsurfrules, .cursor/rules/) into its CWD, and that must never be the user's
    # project or the payload clone.
    $InitDir = Join-Path ([System.IO.Path]::GetTempPath()) ("project-context-init-" + [System.Guid]::NewGuid())
    $InitRan = $false
    try {
        New-Item -ItemType Directory -Force -Path $InitDir | Out-Null
        Push-Location $InitDir
        try { Quiet { jcodemunch-mcp init --client auto --yes } | Out-Null; $InitRan = $true } finally { Pop-Location }
    } catch {
        # A temp-dir failure mirrors install.sh: registration degrades to a warning,
        # it never aborts the install and never runs init from the user's directory.
    }
    if ($InitRan -and $LASTEXITCODE -eq 0) {
        Say "registered the jCodeMunch MCP server with your detected agents"
    } else {
        Say "warning: automatic MCP registration failed; run manually: jcodemunch-mcp init --client auto --yes"
    }
}

& $Python (Join-Path $Payload "scripts\project_context.py") self-check --skill-root $Payload | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "self-check failed; the installation is incomplete. Re-run the installer or file an issue." }

Say "installed $Version and verified with self-check."
Say "next: open Claude Code (or Codex) in any Git repository and say: audit this repository with project-context"
Say "update later by re-running this same command."
