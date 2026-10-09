param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Build', 'Test', 'Migrate', 'Start', 'ResetAdmin')]
    [string]$Action,
    [switch]$ConfirmResetAdmin
)

$ErrorActionPreference = 'Stop'
$serverRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $serverRoot
try {
    if ($Action -in @('Build', 'Test')) {
        if ($Action -eq 'Build') { & mvn -B -ntp clean verify }
        else { & mvn -B -ntp test }
        if ($LASTEXITCODE -ne 0) { throw "Maven exited with code $LASTEXITCODE" }
        return
    }
    $jar = Join-Path $serverRoot 'target/rekindle-server-0.1.0-SNAPSHOT.jar'
    if (-not (Test-Path -LiteralPath $jar)) { throw 'Run Build first.' }
    switch ($Action) {
        'Migrate' { & java -jar $jar --migrate }
        'Start' { & java -jar $jar }
        'ResetAdmin' {
            if (-not $ConfirmResetAdmin) { throw 'ResetAdmin requires -ConfirmResetAdmin.' }
            & java -jar $jar --reset-admin --confirm-reset-admin
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Server exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
