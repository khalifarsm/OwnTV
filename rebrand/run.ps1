# Build or pull the OwnTV rebrand container and run the single-variant pipeline
# (arm release = owntv.apk + x86_64 emulator release = owntv-x86_64.apk).
#
# Credentials are passed via BuildKit secret mounts at build time and env vars at
# run time - they are never baked into the image:
#   - GPR_USER + GPR_TOKEN resolve tv.own.owntv:core/:player-core from GitHub Packages.
#   - GH_TOKEN pushes the APKs into the target repo (skipped with -NoPush).
#
# First release: run without the KEYSTORE_* variables to generate a fresh keystore
# (kept in <work>\out\signing.txt + .jks). For every later release reuse it so the
# signature stays stable (required for OTA updates):
#   .\rebrand\run.ps1 -KeystoreB64 (Get-Content ..\signing.jks.b64 -Raw) -KeystoreAlias <alias> -KeystorePass <pass>
#
# Examples:
#   .\rebrand\run.ps1                          (uses $env:GH_TOKEN / GPR creds / rebrand\.env or prompts)
#   .\rebrand\run.ps1 -GitToken "ghp_..." -GprToken "ghp_..." -NoPush
#   .\rebrand\run.ps1 -FromImage "ghcr.io/khalifarsm/owntv-rebrand:latest"
#   .\rebrand\run.ps1 -DryRun                   (renames a copy without building/keytool/push)
#
# For local testing without pushing, set -SourceDir to a folder INSIDE the work dir:
#   $wd = Join-Path $env:TEMP 'owntv-work-xyz'; New-Item -ItemType Directory $wd | Out-Null
#   Copy-Item -Recurse .\OwnTV $wd\src-local   (clone without the giant build dirs)
#   .\rebrand\run.ps1 -SourceDir '/work/src-local' -NoPush
param(
    [string]$GitToken,
    [string]$GprUser,
    [string]$GprToken,
    [string]$KeystoreB64,
    [string]$KeystoreAlias,
    [string]$KeystorePass,
    [string]$TargetRepo,
    [string]$SourceRepo,
    [string]$SourceBranch,
    [string]$SourceDir,
    [string]$VersionName,
    [string]$VersionCode,
    [switch]$NoPush,
    [switch]$DryRun,
    [string]$FromImage,
    [string]$WorkDir
)
$ErrorActionPreference = "Stop"

# Optional .env file next to this script (name=value lines). Lets you keep the tokens
# out of the shell history / chat. Values set here are only for the duration of the run.
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content -LiteralPath $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
}

if (-not $GitToken) { $GitToken = $env:GH_TOKEN }
if (-not $GprUser)  { $GprUser = $env:GPR_USER }
if (-not $GprUser)  { $GprUser = $env:GITHUB_ACTOR }
if (-not $GprToken) { $GprToken = $env:GPR_TOKEN }
if (-not $KeystoreB64)  { $KeystoreB64  = $env:KEYSTORE_B64 }
if (-not $KeystoreAlias){ $KeystoreAlias = $env:KEYSTORE_ALIAS }
if (-not $KeystorePass) { $KeystorePass  = $env:KEYSTORE_PASS }
if (-not $GprUser)  { $GprUser = Read-Host "GitHub Packages username (GPR_USER / GITHUB_ACTOR)" }
if (-not $GprToken) { $GprToken = Read-Host "GitHub Packages read token (GPR_TOKEN)" }
if (-not $GitToken -and -not $NoPush) { $GitToken = Read-Host "GitHub token (GH_TOKEN)" }

# Surface the credentials to `docker build --secret id=X,env=Y`.
if ($GitToken) { [Environment]::SetEnvironmentVariable("GH_TOKEN", $GitToken, "Process") }
if ($GprUser)  { [Environment]::SetEnvironmentVariable("GPR_USER", $GprUser, "Process") }
if ($GprToken) { [Environment]::SetEnvironmentVariable("GPR_TOKEN", $GprToken, "Process") }
if (-not $WorkDir) {
    $WorkDir = Join-Path $env:TEMP "owntv-work-$([guid]::NewGuid().ToString('N').Substring(0,8))"
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Write-Host "Work dir: $WorkDir"

if ($FromImage) {
    docker pull $FromImage | Out-Host
} else {
    $buildSecretArgs = @()
    if ($GitToken) { $buildSecretArgs += @("--secret", "id=gh_token,env=GH_TOKEN") }
    if ($GprUser)  { $buildSecretArgs += @("--secret", "id=gpr_user,env=GPR_USER") }
    if ($GprToken) { $buildSecretArgs += @("--secret", "id=gpr_token,env=GPR_TOKEN") }
    docker build @buildSecretArgs -t owntv-rebrand "$PSScriptRoot" | Out-Host
}

$dargs = @("--rm")
$dargs += @("-e", "GH_TOKEN=$GitToken")
$dargs += @("-e", "GPR_USER=$GprUser")
$dargs += @("-e", "GPR_TOKEN=$GprToken")
$dargs += @("-v", "${WorkDir}:/work")
if ($TargetRepo)   { $dargs += @("-e", "TARGET_REPO=$TargetRepo") }
if ($SourceRepo)   { $dargs += @("-e", "SOURCE_REPO=$SourceRepo") }
if ($SourceBranch) { $dargs += @("-e", "SOURCE_BRANCH=$SourceBranch") }
if ($SourceDir)    { $dargs += @("-e", "SOURCE_DIR=$SourceDir") }
if ($VersionName)  { $dargs += @("-e", "VERSION_NAME=$VersionName") }
if ($VersionCode)  { $dargs += @("-e", "VERSION_CODE=$VersionCode") }
if ($KeystoreB64)  { $dargs += @("-e", "KEYSTORE_B64=$KeystoreB64") }
if ($KeystoreAlias){ $dargs += @("-e", "KEYSTORE_ALIAS=$KeystoreAlias") }
if ($KeystorePass) { $dargs += @("-e", "KEYSTORE_PASS=$KeystorePass") }
if ($FromImage) {
    $dargs += @($FromImage)
} else {
    $dargs += @("owntv-rebrand")
}
if ($DryRun) { $dargs += @("--dry-run") }
if ($NoPush) { $dargs += @("--no-push") }

docker run @dargs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Artifacts (keystores, signing.txt, apks): $WorkDir\out"