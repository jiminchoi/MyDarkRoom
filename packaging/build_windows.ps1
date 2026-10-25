param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not [Environment]::Is64BitProcess -or $env:OS -ne 'Windows_NT') { throw 'Build with 64-bit Python on Windows.' }
if (-not $SkipInstall) {
    python -m pip install --disable-pip-version-check -r requirements-build.txt
    if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
}
python -m pip check
if ($LASTEXITCODE) { throw 'Dependency verification failed.' }
New-Item -ItemType Directory -Force release | Out-Null
python -m PyInstaller --noconfirm --clean packaging/MyDarkRoom.spec 2>&1 | Tee-Object -FilePath release/pyinstaller.log
if ($LASTEXITCODE) {
    $tail = (Get-Content release/pyinstaller.log -Tail 65) -join "`n"
    $encoded = $tail.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
    Write-Output "::error title=PyInstaller packaging error::$encoded"
    throw 'EXE build failed. See the packaging error annotation.'
}
Copy-Item packaging/Windows-README.txt dist/MyDarkRoom/README-Windows.txt
python packaging/collect_notices.py dist/MyDarkRoom
if ($LASTEXITCODE) { throw 'License collection failed.' }
Write-Host 'Built dist/MyDarkRoom/MyDarkRoom.exe. Smoke-test the whole folder before packaging.'
