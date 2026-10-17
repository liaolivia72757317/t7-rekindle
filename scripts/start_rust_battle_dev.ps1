[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$StageDirectory,

    [Parameter(Mandatory = $true)]
    [string]$CapturePath
)

$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\', '/')
$stageRoot = Join-Path $repoRoot 'artifacts\test-results'
$stage = [System.IO.Path]::GetFullPath($StageDirectory).TrimEnd('\', '/')
$pathComparison = [System.StringComparison]::OrdinalIgnoreCase
if (-not $stage.StartsWith($stageRoot + '\', $pathComparison)) {
    throw 'StageDirectory must be an isolated directory under artifacts\test-results.'
}

if (-not [System.IO.Path]::IsPathRooted($CapturePath) -or $CapturePath -match '^[A-Za-z]:[^\\/]') {
    throw 'CapturePath must be an absolute path.'
}
$capture = [System.IO.Path]::GetFullPath($CapturePath)
if (-not $capture.EndsWith('.jsonl', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'CapturePath must name a .jsonl file.'
}
if ($capture.StartsWith($repoRoot + '\', $pathComparison)) {
    throw 'CapturePath must be outside the repository.'
}
if (-not (Test-Path -LiteralPath (Split-Path -Parent $capture) -PathType Container)) {
    throw 'CapturePath parent directory does not exist.'
}
if (Test-Path -LiteralPath $capture) {
    throw 'CapturePath already exists; choose a new file name.'
}

$launcher = Join-Path $stage 'T7-Rekindle.exe'
foreach ($name in @('T7-Rekindle.exe', 'T7R.NativeBridge.dll', 't7r_battle_ffi.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $stage $name) -PathType Leaf)) {
        throw "Isolated stage is missing $name."
    }
}

$running = Get-Process -Name 'T7-Rekindle' -ErrorAction SilentlyContinue |
    Where-Object { [string]::Equals($_.Path, $launcher, $pathComparison) }
if ($running) {
    throw 'This isolated launcher is already running.'
}

Write-Output "stage=$stage"
Write-Output "capture=$capture"
if ($PSCmdlet.ShouldProcess($launcher, 'Start Rust battle development session')) {
    $previousCapture = [System.Environment]::GetEnvironmentVariable('T7R_BATTLE_CAPTURE_PATH', 'Process')
    try {
        [System.Environment]::SetEnvironmentVariable('T7R_BATTLE_CAPTURE_PATH', $capture, 'Process')
        $process = Start-Process -FilePath $launcher -WorkingDirectory $stage -PassThru
        Write-Output "launcherPid=$($process.Id)"
    }
    finally {
        [System.Environment]::SetEnvironmentVariable('T7R_BATTLE_CAPTURE_PATH', $previousCapture, 'Process')
    }
}
