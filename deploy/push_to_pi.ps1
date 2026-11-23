# Sends the Argos project from the Windows PC to the Raspberry Pi (SSH), then runs the installer.
#
#   powershell -ExecutionPolicy Bypass -File deploy\push_to_pi.ps1 -Pi eya@192.168.68.60
#   powershell -ExecutionPolicy Bypass -File deploy\push_to_pi.ps1 -Pi eya@argos.local -InstallArgs "--hotspot"
#
# Sent: edge/, backend/ (with the web app and APK), config/, deploy/, calibration/*.py, README.md.
# Not sent: .venv, datasets, the PC's database and clips, Flutter sources.
param(
    [Parameter(Mandatory = $true)][string]$Pi,
    [string]$InstallArgs = "",
    [switch]$NoInstall
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "backend\web\index.html")) {
    Write-Warning "backend\web absent : l'application web ne sera pas servie par le Pi (cd app; flutter build web)."
}

$archive = Join-Path $env:TEMP "argos_pi.tgz"
Write-Host "Archive du projet..."
tar -czf $archive `
    --exclude=".venv" --exclude="__pycache__" --exclude=".pytest_cache" `
    --exclude="*.db" --exclude=".argos_secret" --exclude="clips" `
    --exclude="*.pt" --exclude="*_ncnn_model" `
    --exclude="calibration/datasets" --exclude="calibration/resultats*" `
    edge backend config deploy calibration/calibrate_scb.py README.md
Write-Host ("Taille : {0:N1} Mo" -f ((Get-Item $archive).Length / 1MB))

Write-Host "Copie vers $Pi ..."
scp $archive "${Pi}:/tmp/argos_pi.tgz"
if ($LASTEXITCODE -ne 0) { throw "Échec de la copie (adresse, utilisateur, SSH activé ?)" }
ssh $Pi "mkdir -p ~/argos && tar -xzf /tmp/argos_pi.tgz -C ~/argos && rm /tmp/argos_pi.tgz && sed -i 's/\r$//' ~/argos/deploy/*.sh && echo 'projet copié dans ~/argos'"

if (-not $NoInstall) {
    # -t: shows progress and lets sudo ask for the password
    ssh -t $Pi "cd ~/argos && bash deploy/install_pi.sh $InstallArgs"
}
Remove-Item $archive -ErrorAction SilentlyContinue
