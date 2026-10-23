$ErrorActionPreference = 'Stop'
$repositoryDirectory = Split-Path -Parent $PSScriptRoot
$binDirectory = Join-Path $repositoryDirectory 'mod-manager\bin'
$assetBinDirectory = Join-Path $repositoryDirectory 'mod-manager\assets\bin'
New-Item -ItemType Directory -Path $binDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $assetBinDirectory -Force | Out-Null

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$patchersTemp = Join-Path $tempBase ('tribal-patchers-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $patchersTemp | Out-Null

try {
    $bsdiffZip = Join-Path $patchersTemp 'bsdiff.zip'
    $bsdiffUrl = 'https://github.com/reitowo/bsdiff-win/releases/download/v4.3/bsdiff-v4.3-win-x64-vs2019.zip'
    Invoke-WebRequest -Uri $bsdiffUrl -OutFile $bsdiffZip
    $bsdiffExtracted = Join-Path $patchersTemp 'bsdiff-extracted'
    Expand-Archive -LiteralPath $bsdiffZip -DestinationPath $bsdiffExtracted
    Copy-Item (Join-Path $bsdiffExtracted 'bsdiff.exe') -Destination $binDirectory -Force
    Copy-Item (Join-Path $bsdiffExtracted 'bspatch.exe') -Destination $binDirectory -Force
    Copy-Item (Join-Path $bsdiffExtracted 'bsdiff.exe') -Destination $assetBinDirectory -Force
    Copy-Item (Join-Path $bsdiffExtracted 'bspatch.exe') -Destination $assetBinDirectory -Force

    $xdeltaZip = Join-Path $patchersTemp 'xdelta.zip'
    $xdeltaUrl = 'https://github.com/jmacd/xdelta-gpl/releases/download/v3.0.11/xdelta3-3.0.11-x86_64.exe.zip'
    Invoke-WebRequest -Uri $xdeltaUrl -OutFile $xdeltaZip
    $xdeltaExtracted = Join-Path $patchersTemp 'xdelta-extracted'
    Expand-Archive -LiteralPath $xdeltaZip -DestinationPath $xdeltaExtracted
    $xdExe = Get-ChildItem -LiteralPath $xdeltaExtracted -Filter '*.exe' | Select-Object -First 1
    Copy-Item $xdExe.FullName -Destination (Join-Path $binDirectory 'xdelta3.exe') -Force
    Copy-Item $xdExe.FullName -Destination (Join-Path $assetBinDirectory 'xdelta3.exe') -Force

    Write-Output 'External patcher tools (bsdiff, bspatch, xdelta3) successfully fetched and installed.'
} finally {
    Remove-Item -LiteralPath $patchersTemp -Recurse -Force -ErrorAction SilentlyContinue
}
