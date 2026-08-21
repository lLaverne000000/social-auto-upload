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

function Assert-NoReparseAncestors {
    param([Parameter(Mandatory = $true)][string]$Path)
    $FullPath = [IO.Path]::GetFullPath($Path)
    if (-not $FullPath.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Mutable path escapes the repository: $FullPath"
    }
    $RootItem = Get-Item -LiteralPath $ProjectRoot -Force
    if (-not $RootItem.PSIsContainer -or ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Repository root must be a real directory: $ProjectRoot"
    }
    $RelativePath = $FullPath.Substring($ProjectPrefix.Length)
    $CurrentPath = $ProjectRoot
    foreach ($Component in ($RelativePath -split '[\\/]')) {
        if ([string]::IsNullOrEmpty($Component)) {
            continue
        }
        $CurrentPath = Join-Path $CurrentPath $Component
        $CurrentItem = Get-Item -LiteralPath $CurrentPath -Force -ErrorAction SilentlyContinue
        if ($null -eq $CurrentItem) {
            break
        }
        if ($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Mutable path contains a reparse-point ancestor: $CurrentPath"
        }
    }
}

function Get-SafeMutablePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $FullPath = Get-ContainedPath $RelativePath
    Assert-NoReparseAncestors -Path $FullPath
    return $FullPath
}

function Remove-ContainedDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not $Path.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the repository: $Path"
    }
    Assert-NoReparseAncestors -Path $Path
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (-not $Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing to clean a non-directory or reparse point: $Path"
        }
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Remove-TemporaryDirectoryStrict {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-NoReparseAncestors -Path $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    if (Test-Path -LiteralPath $Path) {
        throw "Temporary output cleanup did not complete: $Path"
    }
}

function Remove-PathNonFatal {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    try {
        Assert-NoReparseAncestors -Path $Path
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Recurse -Force
        }
        if (Test-Path -LiteralPath $Path) {
            throw "cleanup left the path in place"
        }
    } catch {
        Write-Warning "Non-fatal cleanup failure for $Path"
    }
}

function Remove-PublishedFileStrict {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-NoReparseAncestors -Path $Path
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Rollback target is not a regular file: $Path"
        }
        Remove-Item -LiteralPath $Path -Force
    }
    if (Test-Path -LiteralPath $Path) {
        throw "Rollback could not remove newly published file: $Path"
    }
}

function Publish-ArtifactPair {
    param(
        [Parameter(Mandatory = $true)][string]$StagedInstaller,
        [Parameter(Mandatory = $true)][string]$StagedChecksum,
        [Parameter(Mandatory = $true)][string]$OutputInstaller,
        [Parameter(Mandatory = $true)][string]$OutputChecksum
    )
    $TransactionId = [guid]::NewGuid().ToString('N')
    $InstallerBackup = Join-Path (Split-Path -LiteralPath $OutputInstaller -Parent) (".$([IO.Path]::GetFileName($OutputInstaller)).backup-$TransactionId")
    $ChecksumBackup = Join-Path (Split-Path -LiteralPath $OutputChecksum -Parent) (".$([IO.Path]::GetFileName($OutputChecksum)).backup-$TransactionId")
    $InstallerExisted = Test-Path -LiteralPath $OutputInstaller -PathType Leaf
    $ChecksumExisted = Test-Path -LiteralPath $OutputChecksum -PathType Leaf
    $PublicationStarted = $false
    $RollbackFailed = $false
    try {
        if ($InstallerExisted) {
            Copy-Item -LiteralPath $OutputInstaller -Destination $InstallerBackup
        }
        if ($ChecksumExisted) {
            Copy-Item -LiteralPath $OutputChecksum -Destination $ChecksumBackup
        }
        $PublicationStarted = $true
        if ($InstallerExisted) {
            [IO.File]::Replace($StagedInstaller, $OutputInstaller, $null, $true)
        } else {
            Move-Item -LiteralPath $StagedInstaller -Destination $OutputInstaller
        }
        if ($ChecksumExisted) {
            [IO.File]::Replace($StagedChecksum, $OutputChecksum, $null, $true)
        } else {
            Move-Item -LiteralPath $StagedChecksum -Destination $OutputChecksum
        }

        $PublishedHash = (Get-FileHash -LiteralPath $OutputInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
        $PublishedChecksum = Get-Content -LiteralPath $OutputChecksum -Raw
        $ExpectedChecksum = $PublishedHash + "  $([IO.Path]::GetFileName($OutputInstaller))`r`n"
        if ($PublishedChecksum -cne $ExpectedChecksum) {
            throw 'Published installer/checksum pair failed its final hash self-check.'
        }
    } catch {
        $PublicationError = $_
        if ($PublicationStarted) {
            try {
                if ($InstallerExisted) {
                    if (Test-Path -LiteralPath $OutputInstaller) {
                        [IO.File]::Replace($InstallerBackup, $OutputInstaller, $null, $true)
                    } else {
                        Move-Item -LiteralPath $InstallerBackup -Destination $OutputInstaller
                    }
                } elseif (Test-Path -LiteralPath $OutputInstaller) {
                    Remove-PublishedFileStrict -Path $OutputInstaller
                }
                if ($ChecksumExisted) {
                    if (Test-Path -LiteralPath $OutputChecksum) {
                        [IO.File]::Replace($ChecksumBackup, $OutputChecksum, $null, $true)
                    } else {
                        Move-Item -LiteralPath $ChecksumBackup -Destination $OutputChecksum
                    }
                } elseif (Test-Path -LiteralPath $OutputChecksum) {
                    Remove-PublishedFileStrict -Path $OutputChecksum
                }
            } catch {
                $RollbackFailed = $true
                throw "Artifact-pair publication failed and Rollback failed; backups were preserved: $($PublicationError.Exception.Message)"
            }
        }
        throw $PublicationError
    } finally {
        if (-not $RollbackFailed) {
            Remove-PathNonFatal -Path $InstallerBackup
            Remove-PathNonFatal -Path $ChecksumBackup
        }
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
$FrontendDist = Get-SafeMutablePath 'sau_frontend\dist'
$FrontendNodeModules = Get-SafeMutablePath 'sau_frontend\node_modules'
$BrowserStage = Get-SafeMutablePath 'packaging\browser-stage'
$BuildDirectory = Get-SafeMutablePath 'build'
$PyInstallerWork = Get-SafeMutablePath 'build\pyinstaller-windows-x64'
$DistDirectory = Get-SafeMutablePath 'dist'
$PayloadDirectory = Get-SafeMutablePath 'dist\SocialAutoUpload'
$SpecPath = (Resolve-Path -LiteralPath (Get-ContainedPath 'packaging\pyinstaller\social_auto_upload.spec')).Path
$IssPath = (Resolve-Path -LiteralPath (Get-ContainedPath 'packaging\windows\SocialAutoUpload.iss')).Path
$ReleaseDirectory = Get-SafeMutablePath 'release'
$InstallerName = 'SocialAutoUpload-Windows-x64-Setup.exe'
$ChecksumName = "$InstallerName.sha256"
$OutputInstaller = Join-Path $ReleaseDirectory $InstallerName
$OutputChecksum = Join-Path $ReleaseDirectory $ChecksumName

Assert-NoReparseAncestors -Path $FrontendNodeModules
Assert-NoReparseAncestors -Path $FrontendDist
Push-Location $FrontendDirectory
try {
    Invoke-NativeCommand -FilePath $NpmPath -ArgumentList @('ci')
    Invoke-NativeCommand -FilePath $NpmPath -ArgumentList @('run', 'build')
} finally {
    Pop-Location
}

Assert-NoReparseAncestors -Path $BrowserStage
Invoke-NativeCommand -FilePath $PythonPath -ArgumentList @(
    '-m', 'release_tools.stage_browser',
    '--source', $BrowserSourcePath,
    '--target', $BrowserStage,
    '--platform', 'windows',
    '--arch', 'x86_64',
    '--revision', $BrowserRevision
)

Assert-NoReparseAncestors -Path $BuildDirectory
Assert-NoReparseAncestors -Path $DistDirectory
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

Assert-NoReparseAncestors -Path $ReleaseDirectory
if (-not (Test-Path -LiteralPath $ReleaseDirectory)) {
    New-Item -ItemType Directory -Path $ReleaseDirectory | Out-Null
}
Assert-NoReparseAncestors -Path $ReleaseDirectory
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
if (Test-Path -LiteralPath $OutputChecksum) {
    $ExistingChecksum = Get-Item -LiteralPath $OutputChecksum -Force
    if ($ExistingChecksum.PSIsContainer -or ($ExistingChecksum.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to replace a non-file or reparse point: $OutputChecksum"
    }
}

$OutputTransactionId = [guid]::NewGuid().ToString('N')
$TemporaryOutputDirectory = Join-Path $ReleaseDirectory ('.SocialAutoUpload-Windows-x64-Setup.' + $OutputTransactionId)
$TemporaryInstaller = Join-Path $TemporaryOutputDirectory $InstallerName
$TemporaryChecksum = Join-Path $TemporaryOutputDirectory $ChecksumName
$StagedInstaller = Join-Path $ReleaseDirectory (".$InstallerName.publish-$OutputTransactionId")
$StagedChecksum = Join-Path $ReleaseDirectory (".$ChecksumName.publish-$OutputTransactionId")
Assert-NoReparseAncestors -Path $TemporaryOutputDirectory
Assert-NoReparseAncestors -Path $StagedInstaller
Assert-NoReparseAncestors -Path $StagedChecksum
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
    $ChecksumLine = $InstallerHash.Hash.ToLowerInvariant() + "  $InstallerName`r`n"
    [IO.File]::WriteAllText($TemporaryChecksum, $ChecksumLine, [Text.Encoding]::ASCII)
    if ((Get-Content -LiteralPath $TemporaryChecksum -Raw) -cne $ChecksumLine) {
        throw 'Temporary installer checksum file failed its content check.'
    }
    $Authenticode = Get-AuthenticodeSignature -FilePath $TemporaryInstaller
    Write-Host ("Installer SHA256: {0}" -f $InstallerHash.Hash.ToLowerInvariant())
    Write-Host ("Authenticode status: {0}" -f $Authenticode.Status)

    Move-Item -LiteralPath $TemporaryInstaller -Destination $StagedInstaller
    Move-Item -LiteralPath $TemporaryChecksum -Destination $StagedChecksum
    Remove-TemporaryDirectoryStrict -Path $TemporaryOutputDirectory
    Publish-ArtifactPair `
        -StagedInstaller $StagedInstaller `
        -StagedChecksum $StagedChecksum `
        -OutputInstaller $OutputInstaller `
        -OutputChecksum $OutputChecksum
} finally {
    Remove-PathNonFatal -Path $TemporaryOutputDirectory
    Remove-PathNonFatal -Path $StagedInstaller
    Remove-PathNonFatal -Path $StagedChecksum
}

Write-Host "Created Windows x64 installer: $OutputInstaller"
Write-Host "Created Windows x64 checksum: $OutputChecksum"
