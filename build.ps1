$ErrorActionPreference = 'Stop'
$releaseDirectory = Join-Path $PSScriptRoot 'release\1.1.0'
New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
& (Join-Path $PSScriptRoot 'mod-manager\build.ps1') -OutputDirectory $releaseDirectory
& (Join-Path $PSScriptRoot 'installer\build.ps1')
