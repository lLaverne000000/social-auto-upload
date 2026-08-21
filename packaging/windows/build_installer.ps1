[CmdletBinding()]
param(
    [string]$BrowserSource = $env:SAU_BROWSER_SOURCE,
    [string]$BrowserRevision = $(if ($env:SAU_BROWSER_REVISION) { $env:SAU_BROWSER_REVISION } else { '1208' }),
    [string]$PythonExecutable = $(if ($env:SAU_PYTHON) { $env:SAU_PYTHON } else { 'python.exe' }),
    [string]$InnoCompiler = $env:SAU_ISCC
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Native command failed with exit code $LASTEXITCODE`: $FilePath"
    }
}

$ScriptDirectory = Split-Path -LiteralPath $PSCommandPath -Parent
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptDirectory '..\..')).Path
$ProjectPrefix = $ProjectRoot.TrimEnd('\') + '\'
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $ProjectRoot
} else {
    $env:PYTHONPATH = $ProjectRoot + [IO.Path]::PathSeparator + $env:PYTHONPATH
}

function Get-ContainedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $FullPath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $RelativePath))
    if (-not $FullPath.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build path escapes the repository: $RelativePath"
    }
    return $FullPath
}

function Remove-ContainedDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not $Path.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the repository: $Path"
    }
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (-not $Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing to clean a non-directory or reparse point: $Path"
        }
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

if ([string]::IsNullOrWhiteSpace($BrowserSource)) {
    throw 'BrowserSource or SAU_BROWSER_SOURCE must name a clean Patchright Chromium directory.'
}

$BrowserSourcePath = (Resolve-Path -LiteralPath $BrowserSource).Path
$BrowserSourceItem = Get-Item -LiteralPath $BrowserSourcePath -Force
if (-not $BrowserSourceItem.PSIsContainer -or
    ($BrowserSourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Browser source must be a real directory: $BrowserSourcePath"
}

$PythonPath = (Get-Command -Name $PythonExecutable -CommandType Application -ErrorAction Stop).Source
$NpmPath = (Get-Command -Name 'npm.cmd' -CommandType Application -ErrorAction Stop).Source
if ([string]::IsNullOrWhiteSpace($InnoCompiler)) {
    $InnoCommand = Get-Command -Name 'ISCC.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $InnoCommand) {
        $InnoCompiler = $InnoCommand.Source
    } else {
        $Candidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        )
        $InnoCompiler = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    }
}
if ([string]::IsNullOrWhiteSpace($InnoCompiler)) {
    throw 'ISCC.exe was not found. Install Inno Setup 6 or set SAU_ISCC.'
}
$InnoCompilerPath = (Resolve-Path -LiteralPath $InnoCompiler).Path

$FrontendDirectory = Get-ContainedPath 'sau_frontend'
$FrontendDist = Get-ContainedPath 'sau_frontend\dist'
$BrowserStage = Get-ContainedPath 'packaging\browser-stage'
$PyInstallerWork = Get-ContainedPath 'build\pyinstaller-windows-x64'
$DistDirectory = Get-ContainedPath 'dist'
$PayloadDirectory = Get-ContainedPath 'dist\SocialAutoUpload'
$SpecPath = (Resolve-Path -LiteralPath (Get-ContainedPath 'packaging\pyinstaller\social_auto_upload.spec')).Path
$IssPath = (Resolve-Path -LiteralPath (Get-ContainedPath 'packaging\windows\SocialAutoUpload.iss')).Path
$ReleaseDirectory = Get-ContainedPath 'release'
$InstallerName = 'SocialAutoUpload-Windows-x64-Setup.exe'
$OutputInstaller = Join-Path $ReleaseDirectory $InstallerName

Push-Location $FrontendDirectory
try {
    Invoke-NativeCommand -FilePath $NpmPath -ArgumentList @('ci')
    Invoke-NativeCommand -FilePath $NpmPath -ArgumentList @('run', 'build')
} finally {
    Pop-Location
}

Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
    '-m', 'release_tools.stage_browser',
    '--source', $BrowserSourcePath,
    '--target', $BrowserStage,
    '--platform', 'windows',
    '--arch', 'x86_64',
    '--revision', $BrowserRevision
)

Remove-ContainedDirectory -Path $PyInstallerWork
Remove-ContainedDirectory -Path $PayloadDirectory
$env:SAU_PROJECT_ROOT = $ProjectRoot
$env:SAU_FRONTEND_DIST = $FrontendDist
$env:SAU_BROWSER_STAGE = $BrowserStage
Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
    '-m', 'PyInstaller',
    '--clean', '--noconfirm',
    '--workpath', $PyInstallerWork,
    '--distpath', $DistDirectory,
    $SpecPath
)

Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
    '-m', 'release_tools.verify_release',
    '--root', $PayloadDirectory,
    '--platform', 'windows',
    '--arch', 'x86_64'
)

if (-not (Test-Path -LiteralPath $ReleaseDirectory)) {
    New-Item -ItemType Directory -Path $ReleaseDirectory | Out-Null
}
$ReleaseItem = Get-Item -LiteralPath $ReleaseDirectory -Force
if (-not $ReleaseItem.PSIsContainer -or ($ReleaseItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "Release path must be a real directory: $ReleaseDirectory"
}
if (Test-Path -LiteralPath $OutputInstaller) {
    $ExistingInstaller = Get-Item -LiteralPath $OutputInstaller -Force
    if ($ExistingInstaller.PSIsContainer -or ($ExistingInstaller.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to replace a non-file or reparse point: $OutputInstaller"
    }
}

$TemporaryOutputDirectory = Join-Path $ReleaseDirectory ('.SocialAutoUpload-Windows-x64-Setup.' + [guid]::NewGuid().ToString('N'))
$TemporaryInstaller = Join-Path $TemporaryOutputDirectory $InstallerName
New-Item -ItemType Directory -Path $TemporaryOutputDirectory | Out-Null
try {
    Invoke-NativeCommand -FilePath $InnoCompilerPath -ArgumentList @(
        "/DPayloadDir=$PayloadDirectory",
        "/DOutputDir=$TemporaryOutputDirectory",
        '/DAppVersion=0.1.0',
        $IssPath
    )
    if (-not (Test-Path -LiteralPath $TemporaryInstaller -PathType Leaf)) {
        throw "ISCC did not create the expected installer: $TemporaryInstaller"
    }
    $TemporaryItem = Get-Item -LiteralPath $TemporaryInstaller -Force
    if (($TemporaryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $TemporaryItem.Length -le 0) {
        throw "ISCC output is not a non-empty regular file: $TemporaryInstaller"
    }

    $InstallerHash = Get-FileHash -LiteralPath $TemporaryInstaller -Algorithm SHA256
    $Authenticode = Get-AuthenticodeSignature -FilePath $TemporaryInstaller
    Write-Host ("Installer SHA256: {0}" -f $InstallerHash.Hash.ToLowerInvariant())
    Write-Host ("Authenticode status: {0}" -f $Authenticode.Status)

    if (Test-Path -LiteralPath $OutputInstaller) {
        [IO.File]::Replace($TemporaryInstaller, $OutputInstaller, $null, $true)
    } else {
        Move-Item -LiteralPath $TemporaryInstaller -Destination $OutputInstaller
    }
} finally {
    if (Test-Path -LiteralPath $TemporaryOutputDirectory) {
        Remove-Item -LiteralPath $TemporaryOutputDirectory -Recurse -Force
    }
}

Write-Host "Created Windows x64 installer: $OutputInstaller"
