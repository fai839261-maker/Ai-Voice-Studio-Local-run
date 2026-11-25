# Windows PowerShell 5.1 / PowerShell 7 compatible. ASCII source by design.
param([switch]$Offline, [string]$BuildEnvironment = "")
$ErrorActionPreference = 'Stop'
# Run in the current Windows account. WinGet can request elevation for native tools if necessary.
Set-Location -LiteralPath $PSScriptRoot
Start-Transcript -Path (Join-Path $PSScriptRoot 'Build-Windows.log') -Force | Out-Null
Write-Host '===== AI VOICE STUDIO - WINDOWS BUILD =====' -ForegroundColor Cyan

# Windows CreateProcess argument quoting, including spaces, quotes and trailing slashes.
function Convert-NativeArg([string]$arg) {
    if ($arg -ne '' -and $arg -notmatch '[\s"]') { return $arg }
    return '"' + (($arg -replace '(\\*)"','$1$1\"') -replace '(\\+)$','$1$1') + '"'
}
function Invoke-HiddenNative([string]$program,[string[]]$arguments,[switch]$Quiet) {
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $program
    $start.Arguments = (($arguments | ForEach-Object { Convert-NativeArg $_ }) -join ' ')
    $start.WorkingDirectory = $PSScriptRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $proc = New-Object Diagnostics.Process
    $proc.StartInfo = $start
    try {
        if (-not $proc.Start()) { throw "Could not start $program" }
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $stderr = $proc.StandardError.ReadToEndAsync()
        $proc.WaitForExit()
        $script:NativeExit = $proc.ExitCode
        $out = $stdout.GetAwaiter().GetResult()
        $err = $stderr.GetAwaiter().GetResult()
        if ($Quiet) { return $out }
        if ($out) { Write-Host $out }
        if ($err) { Write-Host $err }
    } finally { $proc.Dispose() }
}

function Refresh-ProcessPath {
    $paths = @($env:Path, [Environment]::GetEnvironmentVariable('Path','Machine'), [Environment]::GetEnvironmentVariable('Path','User'))
    $env:Path = (($paths | Where-Object { $_ }) -join ';')
}

function Get-PythonCandidates {
    $paths = @()
    foreach ($name in @('py.exe','python.exe','python3.exe')) {
        $paths += @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    }
    foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles", "${env:ProgramFiles(x86)}", "$env:SystemDrive\")) {
        if (Test-Path -LiteralPath $base -PathType Container) {
            $paths += @(Get-ChildItem -LiteralPath $base -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'python.exe' })
        }
    }
    foreach ($key in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore','HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        if (Test-Path -LiteralPath $key) {
            foreach ($version in @(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue)) {
                $install = Join-Path $version.PSPath 'InstallPath'
                if (Test-Path -LiteralPath $install) {
                    $reg = Get-Item -LiteralPath $install
                    $paths += $reg.GetValue('ExecutablePath')
                    $dir = $reg.GetValue('')
                    if ($dir) { $paths += (Join-Path $dir 'python.exe') }
                }
            }
        }
    }
    $paths += @("$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe", "$env:WINDIR\py.exe")
    $paths | Where-Object { $_ -and $_ -notmatch '\\Microsoft\\WindowsApps\\' -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Unique
}

function Find-RealPython {
    $found = @()
    foreach ($exe in @(Get-PythonCandidates)) {
        $versions = @('')
        if ([IO.Path]::GetFileName($exe) -ieq 'py.exe') { $versions = @('-3.11','-3.12','-3.13') }
        foreach ($v in $versions) {
            try {
                $argList = @()
                if ($v) { $argList += $v }
                $argList += @('-c',"import sys,struct;print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'|'+sys.executable if struct.calcsize('P')==8 else 'BAD')")
                $out = Invoke-HiddenNative $exe $argList -Quiet
                if ($script:NativeExit -eq 0 -and "$out".Trim() -match '^3\.(11|12|13)\|(.+)$') {
                    $minor = [int]$Matches[1]
                    $actual = $Matches[2]
                    if (Test-Path -LiteralPath $actual -PathType Leaf) {
                        $found += [pscustomobject]@{ Path=$actual; Minor=$minor }
                    }
                }
            } catch { continue }
        }
    }
    # Prefer the runtime-supported 3.11/3.12; 3.13 is a build-only fallback.
    $found | Sort-Object Minor | Select-Object -First 1
}

function Install-WithWinget([string]$id) {
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'WinGet is unavailable. Install or update Microsoft App Installer, then rerun.' }
    Write-Host "Installing $id via WinGet. Approve any Windows prompts."
    Invoke-HiddenNative $winget.Source @('install','--id',$id,'--exact','--source','winget','--accept-package-agreements','--accept-source-agreements','--silent','--disable-interactivity')
    $code = $script:NativeExit
    if ($code -ne 0) { Write-Warning "WinGet returned $code. Checking whether the required program is already installed." }
    Refresh-ProcessPath
}

$detected = Find-RealPython
if (-not $detected) {
    Write-Host 'Compatible 64-bit Python 3.11-3.13 not found (Store alias excluded).' -ForegroundColor Yellow
    if ($Offline) { throw 'Offline build requires installed compatible Python. Run BUILD_INSTALLER.bat online once.' }
    Install-WithWinget 'Python.Python.3.11'
    $detected = Find-RealPython
    if (-not $detected) { throw 'No compatible Python was detected after WinGet. Install Python 3.11 x64 or restart and try again.' }
}
$python = $detected.Path
Write-Host "Using build interpreter: $python"
# Audit all source inputs before downloading packages.
$must = @('main.py','avs_boot.py','studio\\updater.py','requirements-gui.txt','installer\AI_Voice_Studio.iss','studio\core.py','studio\preflight.py','studio\update_local.py','studio\source_update.py','AVS_SOURCE_UPDATE.json','studio\environment_audit.py','studio\ui_v3.py','studio\ui.py','studio\ui_v2.py','studio\managed.py','studio\external.py','studio\setup_queue.py','studio\services.py','resources\avs.ico',
'studio\migration.py','studio\move_engine.py','studio\robocopy_engine.py','studio\move_worker.py','resources\catalog.json','runtime\runner.py','runtime\adapters\piper_adapter.py','runtime\adapters\kokoro_adapter.py','runtime\adapters\onnx_inputs.py','runtime\adapters\vits_adapter.py',
'runtime\adapters\qwen_adapter.py','runtime\adapters\turbo_adapter.py','runtime\adapters\voxcpm_adapter.py','runtime\adapters\openf5_adapter.py','runtime\adapters\chatterbox_adapter.py','runtime\adapters\f5_adapter.py','runtime\requirements-base-cpu.txt','runtime\requirements-base-cuda.txt')
foreach ($file in $must) { if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "MISSING build resource: $file" } }

# Discover build tools without requesting a folder from the user.
$data = if ($env:AI_VOICE_STUDIO_HOME) { $env:AI_VOICE_STUDIO_HOME } else { Join-Path $env:LOCALAPPDATA 'AVS' }
$cache = Join-Path $data 'build-tools'
New-Item -ItemType Directory -Path $cache -Force | Out-Null
# The OS releases this exclusive lock when the build process exits, including failures.
$buildLock = [IO.File]::Open((Join-Path $cache 'build.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$managedVenv = Join-Path $cache '.build-venv'
function Test-BuildEnvironment([string]$candidate) {
    if (-not $candidate) { return $false }
    $candidatePython = Join-Path $candidate 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $candidatePython -PathType Leaf)) { return $false }
    try {
        $result = Invoke-HiddenNative $candidatePython @('-c',"import sys,struct;print('OK' if (3,11)<=sys.version_info[:2]<=(3,13) and struct.calcsize('P')==8 else 'BAD')") -Quiet
        return ($script:NativeExit -eq 0 -and "$result".Trim() -eq 'OK')
    } catch { return $false }
}
$venv = $null
$cachedAlternates = @(Get-ChildItem -LiteralPath $cache -Directory -Filter '.build-venv-*' -ErrorAction SilentlyContinue | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Sort-Object LastWriteTime -Descending | ForEach-Object { $_.FullName })
foreach ($candidate in (@($BuildEnvironment, (Join-Path $PSScriptRoot '.build-venv'), $managedVenv) + $cachedAlternates)) {
    if (Test-BuildEnvironment $candidate) { $venv=$candidate; break }
}
if (-not $venv) {
    if ($Offline) { throw 'No compatible cached build environment. Run the update launcher online once.' }
    $venv = $managedVenv
    # Preserve a damaged environment rather than deleting any source folder or runtime.
    if (Test-Path -LiteralPath $venv) {
        $venv = Join-Path $cache ('.build-venv-' + [guid]::NewGuid().ToString('N'))
    }
    Invoke-HiddenNative $python @('-m','venv',$venv)
    if ($script:NativeExit -ne 0 -or -not (Test-BuildEnvironment $venv)) { throw 'Could not create managed build environment.' }
    # Reuse this alternate cache on later builds even if the canonical cache is broken.
}
$buildPython = Join-Path $venv 'Scripts\python.exe'
# Reuse the previous build environment instead of downloading packages again.
$buildReady = $false
try {
    Invoke-HiddenNative $buildPython @('-c',"import PySide6,psutil,PyInstaller,imageio_ffmpeg,truststore,certifi;from importlib.metadata import version;from packaging.version import Version;assert Version('6.7')<=Version(version('PySide6'))<Version('7');assert Version('6.10')<=Version(version('PyInstaller'))<Version('7');assert Version('6')<=Version(version('psutil'))<Version('8');assert Version('0.6')<=Version(version('imageio-ffmpeg'))<Version('1')") -Quiet | Out-Null
    $buildReady = ($script:NativeExit -eq 0)
} catch { $buildReady = $false }
if ($buildReady) {
    Invoke-HiddenNative $buildPython @('-m','pip','check')
    $buildReady = ($script:NativeExit -eq 0)
}
if (-not $buildReady) {
    if ($Offline) { throw 'Existing .build-venv is incomplete. Extract the update into the previous source folder or run BUILD_INSTALLER.bat online once.' }
    Invoke-HiddenNative $buildPython @('-m','pip','install','--disable-pip-version-check','--retries','7','--timeout','120','-r','requirements-gui.txt')
    if ($script:NativeExit -ne 0) { throw 'GUI/build package installation failed.' }
} else { Write-Host 'Reusing existing compatible build packages; no package download required.' -ForegroundColor Green }

# Build an independent helper that runs while every application file is unlocked.
Invoke-HiddenNative $buildPython @('-m','PyInstaller','--noconfirm','--windowed','--onefile','--name','AVS_Move_Helper','--paths','.','--icon','resources\avs.ico','--collect-all','psutil','studio\move_worker.py')
if ($script:NativeExit -ne 0) { throw 'Move folder helper compilation failed.' }
$moveHelper = Join-Path $PSScriptRoot 'dist\AVS_Move_Helper.exe'
if (-not (Test-Path -LiteralPath $moveHelper -PathType Leaf)) { throw 'Missing compiled Move folder helper.' }
Invoke-HiddenNative $moveHelper @('--self-test')
if ($script:NativeExit -ne 0) { throw 'Compiled Move folder helper self-test failed.' }

Invoke-HiddenNative $buildPython @('-m','PyInstaller','--noconfirm','--windowed','--onedir','--name','AI_Voice_Studio','--icon','resources\avs.ico','--add-data','resources;resources','--add-data',"$moveHelper;resources",'--add-data','runtime;runtime','--add-data','studio;studio_src','--hidden-import','zipfile', '--hidden-import','wave', '--hidden-import','array', '--hidden-import','atexit', '--hidden-import','hmac', '--hidden-import','secrets', '--hidden-import','difflib', '--hidden-import','textwrap', '--hidden-import','fnmatch', '--hidden-import','csv', '--hidden-import','html', '--hidden-import','http.client', '--hidden-import','xml.etree.ElementTree', '--hidden-import','sqlite3', '--hidden-import','decimal', '--hidden-import','statistics', '--hidden-import','random', '--hidden-import','uuid', '--hidden-import','ipaddress', '--hidden-import','getpass', '--hidden-import','bisect', '--hidden-import','heapq', '--hidden-import','queue', '--hidden-import','shutil', '--hidden-import','tempfile', '--hidden-import','glob', '--hidden-import','pprint', '--hidden-import','string', '--hidden-import','email.utils', '--hidden-import','urllib.parse', '--hidden-import','base64', '--hidden-import','binascii', '--hidden-import','calendar', '--hidden-import','collections.abc','--collect-all','psutil','--collect-all','imageio_ffmpeg','--collect-all','truststore','--collect-all','certifi','main.py')
if ($script:NativeExit -ne 0) { throw 'PyInstaller failed.' }

$exe = Join-Path $PSScriptRoot 'dist\AI_Voice_Studio\AI_Voice_Studio.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Missing compiled executable.' }
Write-Host 'Testing bundled resources from the compiled Windows executable...'
# A windowed executable must be explicitly waited on. Use a fresh isolated test directory.
$checkDir = Join-Path ([IO.Path]::GetTempPath()) ('AVS-build-check-' + [guid]::NewGuid().ToString('N'))
$oldHome = $env:AI_VOICE_STUDIO_HOME
try {
    $env:AI_VOICE_STUDIO_HOME = $checkDir
    Invoke-HiddenNative $exe @('--self-check')
    $checkPath = Join-Path $checkDir 'packaged-self-check.json'
    if ($script:NativeExit -ne 0 -or -not (Test-Path -LiteralPath $checkPath)) { throw 'Packaged self-check failed or did not produce a report.' }
    $check = Get-Content -LiteralPath $checkPath -Raw | ConvertFrom-Json
    if ($check.result -ne 'PASS' -or @($check.missing).Count -ne 0 -or $check.version -ne '0.23.0') { throw 'Packaged resource test failed.' }
    $reportDir = Join-Path $PSScriptRoot 'BuildOut'
    New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
    Copy-Item -LiteralPath $checkPath -Destination (Join-Path $reportDir 'packaged-self-check.json') -Force
} finally {
    $env:AI_VOICE_STUDIO_HOME = $oldHome
    if (Test-Path -LiteralPath $checkDir) { Remove-Item -LiteralPath $checkDir -Recurse -Force }
}

function Find-InnoSetup {
    $cmd = Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source -PathType Leaf)) { return $cmd.Source }
    foreach ($base in @("$env:LOCALAPPDATA\Programs", "$env:ProgramFiles", "${env:ProgramFiles(x86)}")) {
        if (Test-Path -LiteralPath $base -PathType Container) {
            foreach ($dir in @(Get-ChildItem -LiteralPath $base -Filter 'Inno Setup*' -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
                $candidate = Join-Path $dir.FullName 'ISCC.exe'
                if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
            }
        }
    }
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (Test-Path -LiteralPath $key) {
            foreach ($entry in @(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue)) {
                $info = Get-ItemProperty -LiteralPath $entry.PSPath -ErrorAction SilentlyContinue
                if ($info.DisplayName -like 'Inno Setup*' -and $info.InstallLocation) {
                    $candidate = Join-Path $info.InstallLocation 'ISCC.exe'
                    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
                }
            }
        }
    }
    return $null
}
$iscc = Find-InnoSetup
if (-not $iscc) {
    if ($Offline) { throw 'Offline build requires installed Inno Setup. Run BUILD_INSTALLER.bat online once.' }
    Install-WithWinget 'JRSoftware.InnoSetup'
    $iscc = Find-InnoSetup
    if (-not $iscc) { throw 'Inno Setup compiler not found after WinGet. Install Inno Setup 6.3 or newer and rerun.' }
}
Write-Host "Compiling with: $iscc"
$final = Join-Path $PSScriptRoot 'BuildOut\AI_Voice_Studio_Setup_0.23.0.exe'
# Do not mistake a previous installer for the result of this build.
if (Test-Path -LiteralPath $final) { Remove-Item -LiteralPath $final -Force }
Invoke-HiddenNative $iscc @('installer\AI_Voice_Studio.iss')
if ($script:NativeExit -ne 0) { throw 'Inno Setup compilation failed.' }
if (-not (Test-Path -LiteralPath $final -PathType Leaf)) { throw 'Final installer did not appear.' }
(Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash | Set-Content -LiteralPath ($final + '.sha256.txt') -Encoding ASCII
$updateZip = Join-Path $PSScriptRoot 'BuildOut\AI_Voice_Studio_Update_0.23.0.zip'
Compress-Archive -LiteralPath @($final, ($final + '.sha256.txt')) -DestinationPath $updateZip -Force
Write-Host "Offline update ZIP: $updateZip"
# A clearly named copy of the installer sits OUTSIDE the project folder, next to it.
$outside = Join-Path (Split-Path $PSScriptRoot -Parent) 'AI Voice Studio by Unzip Spark Setup.exe'
Copy-Item -LiteralPath $final -Destination $outside -Force
Write-Host "Installer copy: $outside" -ForegroundColor Green
Write-Host "SUCCESS - Installer: $final" -ForegroundColor Green
Write-Host 'IMPORTANT: Real Windows installation, model download and WAV generation testing are still required before release.'

$buildLock.Dispose()
Stop-Transcript | Out-Null
