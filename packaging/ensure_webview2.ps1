$ErrorActionPreference = 'Stop'
$key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$present = @("HKCU:\Software\$key", "HKLM:\Software\WOW6432Node\$key", "HKLM:\Software\$key") | Where-Object {
    (Get-ItemProperty -Path $_ -Name pv -ErrorAction SilentlyContinue).pv -match '^[1-9]'
}
if ($present) { Write-Host 'Microsoft WebView2 Runtime is installed.'; exit 0 }
$installer = Join-Path $env:RUNNER_TEMP 'MicrosoftEdgeWebview2Setup.exe'
Invoke-WebRequest 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $installer
$signature = Get-AuthenticodeSignature $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'Invalid Microsoft runtime installer signature.' }
$process = Start-Process -FilePath $installer -ArgumentList '/silent', '/install' -Wait -PassThru
if ($process.ExitCode -notin @(0, 3010)) { throw "WebView2 installation failed: $($process.ExitCode)" }
