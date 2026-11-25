# Copy only this release's source files into the existing build project, then build offline.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$picker = New-Object System.Windows.Forms.FolderBrowserDialog
$picker.Description = 'Select your existing AI_Voice_Studio_Project folder (the one that built your previous version).'
$picker.ShowNewFolderButton = $false
if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
$target = [IO.Path]::GetFullPath($picker.SelectedPath).TrimEnd('\')
$source = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $target 'Build-Windows.ps1')) -or -not (Test-Path -LiteralPath (Join-Path $target 'studio\core.py'))) { throw 'This is not an existing AI Voice Studio source project. Select the project folder, not the installed application folder.' }
if ($target -ne $source) {
    $backup = Join-Path $target ('.avs-source-backup\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $items = @()
    foreach ($folder in @('studio','runtime','installer','resources','tests','.github')) {
        $dir = Join-Path $source $folder
        if (Test-Path -LiteralPath $dir) {
            $items += @(Get-ChildItem -LiteralPath $dir -Recurse -File -Force | Where-Object { $_.FullName -notmatch '\\__pycache__\\' -and $_.Extension -ne '.pyc' })
        }
    }
    foreach ($file in @('main.py','requirements-gui.txt','README.md','WINDOWS_RELEASE_CHECKLIST.md','BUILD_INSTALLER.bat','BUILD_OFFLINE_UPDATE.bat','Build-Windows.ps1','APPLY_OFFLINE_UPDATE.bat','Apply-Offline-Update.ps1','OFFLINE_UPDATE_README.txt','VALIDATION.txt','AVS_SOURCE_UPDATE.json','UPDATE_MY_SOFTWARE.vbs','Upgrade-Installed.ps1','SOURCE_UPDATE_README.txt')) {
        $item = Join-Path $source $file
        if (Test-Path -LiteralPath $item) { $items += Get-Item -LiteralPath $item }
    }
    foreach ($item in $items) {
        $rel = $item.FullName.Substring($source.Length + 1)
        $dest = Join-Path $target $rel
        if (Test-Path -LiteralPath $dest -PathType Leaf) {
            $saved = Join-Path $backup $rel
            New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null
            Copy-Item -LiteralPath $dest -Destination $saved -Force
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $dest -Force
    }
    Write-Host 'Updated source files. Previous source backed up; build environment and installed data are unchanged.' -ForegroundColor Green
}
Write-Host 'Starting local offline build...'
$builder = Join-Path $target 'Build-Windows.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Offline
exit $LASTEXITCODE
