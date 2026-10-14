$ErrorActionPreference = 'Stop'
$repositoryDirectory = Split-Path -Parent $PSScriptRoot
$compilerDirectory = Join-Path $repositoryDirectory 'mod-manager\tools\umt'
if (Test-Path -LiteralPath $compilerDirectory) { throw 'Compiler directory already exists. Use the existing official distribution or choose a clean checkout.' }
$compilerUrl = 'https://github.com/UnderminersTeam/UndertaleModTool/releases/download/0.9.2.0/UTMT_CLI_v0.9.2.0-Windows.zip'
$expectedHash = 'e7573e45d107be34f81f955c6e4afc3c7c8f2628e5a6f307a871e3825b3dfb40'
$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$compilerTemp = Join-Path $tempBase ('tribal-manager-compiler-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $compilerTemp | Out-Null
try {
    $compilerZip = Join-Path $compilerTemp 'compiler.zip'
    Invoke-WebRequest -Uri $compilerUrl -OutFile $compilerZip
    if ((Get-FileHash -LiteralPath $compilerZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Compiler download checksum did not match the official release.' }
    $extracted = Join-Path $compilerTemp 'extracted'
    Expand-Archive -LiteralPath $compilerZip -DestinationPath $extracted
    $executables = @(Get-ChildItem -LiteralPath $extracted -Filter UndertaleModCli.exe -File -Recurse)
    if ($executables.Count -ne 1) { throw 'Expected one UndertaleModCli.exe in the official archive.' }
    $distribution = $executables[0].Directory.FullName
    if (-not (Test-Path -LiteralPath (Join-Path $distribution 'LICENSE.txt'))) { throw 'Compiler license is missing.' }
    New-Item -ItemType Directory -Path (Split-Path -Parent $compilerDirectory) -Force | Out-Null
    Copy-Item -LiteralPath $distribution -Destination $compilerDirectory -Recurse
    Write-Output 'Official compiler prepared with checksum and license verified.'
} finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($compilerTemp)
    if (-not $resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -or -not [System.IO.Path]::GetFileName($resolvedTemp).StartsWith('tribal-manager-compiler-')) { throw 'Unexpected temporary directory; cleanup stopped.' }
    Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
}
