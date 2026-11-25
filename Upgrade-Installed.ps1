# User-approved local upgrade from an extracted, trusted source project.
# Does not uninstall the prior application or remove managed data.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
function Has-AvsExe([string]$Dir) {
    foreach ($name in @('Voice Studio V0.1.exe','AI_Voice_Studio.exe')) { if (Test-Path -LiteralPath (Join-Path $Dir $name) -PathType Leaf) { return $true } }
    return $false
}
function Tell([string]$message, [string]$icon='Information') {
    [void][Windows.Forms.MessageBox]::Show($message,'AI Voice Studio update','OK',$icon)
}
function Ask([string]$message) {
    return ([Windows.Forms.MessageBox]::Show($message,'AI Voice Studio update','YesNo','Question') -eq 'Yes')
}
function Find-InstalledStudio {
    $subkey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{AC43A22B-744E-4969-8CE0-4D9C4A4BDF5A}_is1'
    $paths = @()
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryHive]::LocalMachine)) {
        $paths = @()
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)) {
            $base = $null; $key = $null
            try {
                $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
                $key = $base.OpenSubKey($subkey)
                if ($key) {
                    $dir = [string]$key.GetValue('InstallLocation','')
                    if ($dir) {
                        $dir = [IO.Path]::GetFullPath($dir).TrimEnd('\')
                        if ((Has-AvsExe $dir) -and @(Get-ChildItem -LiteralPath $dir -Filter 'unins*.exe' -File -ErrorAction SilentlyContinue).Count) {
                            $paths += [pscustomobject]@{Path=$dir;Version=[string]$key.GetValue('DisplayVersion','')}
                        } else { throw "Registered AI Voice Studio installation is unavailable: $dir. Restore the drive before updating." }
                    }
                }
            } finally { if ($key) { $key.Dispose() }; if ($base) { $base.Dispose() } }
        }
        $paths = @($paths | Sort-Object Path -Unique)
        if ($paths.Count -gt 1) { throw 'Multiple installation directories were found. Use the updater inside the intended installed copy.' }
        if ($paths.Count -eq 1) { return $paths[0] }
    }
    $running = @(Get-Process -Name 'AI_Voice_Studio','Voice Studio V0.1' -ErrorAction SilentlyContinue)
    $candidates = @($running | ForEach-Object { try { if ($_.Path) { Split-Path -Parent $_.Path } } catch {} })
    $candidates = @($candidates | Sort-Object -Unique)
    if ($running.Count -and $candidates.Count -ne 1) { throw 'Cannot identify the running installation safely. Use its in-app updater.' }
    if ($candidates.Count -eq 1) {
        $dir = $candidates[0]
        if (@(Get-ChildItem -LiteralPath $dir -Filter 'unins*.exe' -File -ErrorAction SilentlyContinue).Count) { return [pscustomobject]@{Path=$dir;Version=''} }
        throw 'The running copy is not a verified installed application.'
    }
    $default = Join-Path $env:LOCALAPPDATA 'Programs\AI Voice Studio'
    if ((Has-AvsExe $default) -and @(Get-ChildItem -LiteralPath $default -Filter 'unins*.exe' -File -ErrorAction SilentlyContinue).Count) { return [pscustomobject]@{Path=$default;Version=''} }
    return $null
}
$mutex = New-Object Threading.Mutex($false,'Local\AI_Voice_Studio_Source_Upgrade')
$locked = $false
$form = $null
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'An upgrade launcher is already running.' }
    $project = $PSScriptRoot
    $manifest = Get-Content -LiteralPath (Join-Path $project 'AVS_SOURCE_UPDATE.json') -Raw | ConvertFrom-Json
    if ($manifest.format -ne 'avs-source-update-v1' -or $manifest.app -ne 'AI Voice Studio' -or $manifest.version -notmatch '^\d{1,5}\.\d{1,5}\.\d{1,5}$') { throw 'Invalid source project manifest.' }
    $version = [string]$manifest.version
    if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'AVS-Move\active.json')) { throw 'Finish Move folder recovery before updating.' }
    $installed = Find-InstalledStudio
    if ($installed -and $installed.Version -match '^\d+\.\d+\.\d+$' -and [version]$installed.Version -gt [version]$version) { throw 'Installed version is newer. Downgrade refused.' }
    $builder = Join-Path $project 'Build-Windows.ps1'
    if (-not (Test-Path -LiteralPath $builder -PathType Leaf)) { throw 'Extract the complete source ZIP first.' }
    if (-not (Ask "Build AI Voice Studio $version and upgrade your existing installation?`n`nOnly continue if you trust this source project. It runs local build code and may download missing Python or Inno Setup components. Existing models are not included or deleted. Windows may show a security prompt.")) { exit 0 }
    $buildlog = Join-Path $project 'Upgrade-Build.log'
    $errlog = Join-Path $project 'Upgrade-Build-errors.log'
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $buildArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $builder + '"'
    $form = New-Object Windows.Forms.Form
    $form.Text = "Building AI Voice Studio $version"
    $form.Size = New-Object Drawing.Size(510,185)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog';$form.MaximizeBox = $false;$form.MinimizeBox = $true;$form.ControlBox = $false
    $info = New-Object Windows.Forms.Label
    $info.SetBounds(20,18,455,50);$info.Text = 'Checking build tools and compiling locally. The installed application will not be replaced until the build succeeds.'
    $bar = New-Object Windows.Forms.ProgressBar
    $bar.SetBounds(20,75,455,16);$bar.Style = 'Marquee';$bar.MarqueeAnimationSpeed = 40
    $status = New-Object Windows.Forms.Label
    $status.SetBounds(20,102,455,32);$status.AutoEllipsis = $true;$status.Text = 'Build log: ' + $buildlog
    $form.Controls.AddRange(@($info,$bar,$status));$form.Show()
    $proc = Start-Process -FilePath $ps -ArgumentList $buildArgs -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $buildlog -RedirectStandardError $errlog -PassThru
    while (-not $proc.HasExited) { [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 300;$proc.Refresh() }
    $proc.WaitForExit();$form.Close();$form.Dispose();$form=$null
    if ($proc.ExitCode -ne 0) { throw "Build failed (exit $($proc.ExitCode)). Read:`n$buildlog`n$errlog`n$(Join-Path $project 'Build-Windows.log')" }
    $setup = Join-Path $project "BuildOut\AI_Voice_Studio_Setup_$version.exe"
    $check = Join-Path $project 'BuildOut\packaged-self-check.json'
    $checksum = $setup + '.sha256.txt'
    foreach ($p in @($setup,$check,$checksum)) { if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Build output missing: $p" } }
    $report=Get-Content -LiteralPath $check -Raw | ConvertFrom-Json
    if ($report.result -ne 'PASS' -or @($report.missing).Count -ne 0 -or $report.version -ne $version) { throw 'Packaged self-check failed. Old installation left unchanged.' }
    if ((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash -ne (Get-Content -LiteralPath $checksum -Raw).Trim()) { throw 'Installer checksum mismatch.' }
    # Do not force-close the old studio: it may be generating or downloading.
    while (@(Get-Process -Name 'AI_Voice_Studio','Voice Studio V0.1' -ErrorAction SilentlyContinue).Count -gt 0) {
        if (-not (Ask "Build succeeded. Save your work, stop downloads and close every AI Voice Studio window. Then click Yes to recheck. No leaves the compiled installer in BuildOut.")) { exit 0 }
    }
    $current = Find-InstalledStudio
    if ($current) {
        if ($installed -and $current.Path -ine $installed.Path) { throw 'Installation location changed during the build. Restart the update.' }
        $installed = $current
    }
    if ($installed) {
        if (-not (Has-AvsExe $installed.Path)) { throw 'Existing installation is no longer available.' }
        if ($installed.Version -match '^\d+\.\d+\.\d+$' -and [version]$installed.Version -gt [version]$version) { throw 'Installed version is newer. Downgrade refused.' }
        if (-not (Ask "Install version $version into the detected installation?`n$($installed.Path)`n`nExisting AVS data are preserved. Back up important files before proceeding.")) { exit 0 }
    } else {
        if (-not (Ask 'No existing installation could be identified. Start a new installation with the regular setup wizard?')) { exit 0 }
    }
    $data = if ($env:AI_VOICE_STUDIO_HOME) { $env:AI_VOICE_STUDIO_HOME } else { Join-Path $env:LOCALAPPDATA 'AVS' }
    $state = Join-Path $data 'state'
    if (Test-Path -LiteralPath (Join-Path $state 'studio.sqlite3') -PathType Leaf) {
        $backup = Join-Path $data ('updates\before-update-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $backup -Force | Out-Null
        Get-ChildItem -LiteralPath $state -File -Filter 'studio.sqlite3*' | Copy-Item -Destination $backup
    }
    # Use the detected logical path (including a migrated junction). Show installer progress, not a console.
    $installArgs = if ($installed) { '/AVSOFFLINE=1 /NORESTART /SILENT /DIR="' + $installed.Path + '"' } else { '/NORESTART' }
    $install = Start-Process -FilePath $setup -ArgumentList $installArgs -PassThru -Wait
    if ($install.ExitCode -ne 0) { throw "Setup returned exit $($install.ExitCode). See Inno Setup log. The compiled installer is in BuildOut." }
    Tell "Setup completed for AI Voice Studio $version. Open the application and confirm the displayed version. Models remain in the existing data folder."
} catch {
    if ($form) { $form.Close();$form.Dispose() }
    Tell $_.Exception.Message 'Error'
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() };$mutex.Dispose()
}
