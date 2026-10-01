$toolchainRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.local\toolchains'
$dotnetRoot = Join-Path $toolchainRoot 'dotnet-8.0.425'
$sdkPath = Join-Path $dotnetRoot 'sdk\8.0.425\Sdks'
$frameworkRoot = Join-Path $toolchainRoot 'net48-reference-assemblies\build'

foreach ($required in @(
    (Join-Path $dotnetRoot 'dotnet.exe'),
    (Join-Path $sdkPath 'Microsoft.NET.Sdk\Sdk\Sdk.props'),
    (Join-Path $frameworkRoot '.NETFramework\v4.8\PresentationFramework.dll')
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Local managed toolchain file is missing: $required"
    }
}

$env:DOTNET_ROOT = $dotnetRoot
$env:PATH = "$dotnetRoot;$env:PATH"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_CLI_HOME = Join-Path $toolchainRoot 'dotnet-home'
$env:NUGET_PACKAGES = Join-Path $toolchainRoot 'nuget-packages'
$env:MSBuildSDKsPath = $sdkPath
$env:MSBuildEnableWorkloadResolver = 'false'
$env:MSBUILDDISABLENODEREUSE = '1'
$env:TargetFrameworkRootPath = $frameworkRoot + '\'
