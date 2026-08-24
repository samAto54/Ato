param(
    [string]$Destination = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $projectRoot 'start-ato.cmd'
$pythonwPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$shortcutPath = Join-Path $Destination 'Ato.lnk'

if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Ato launcher was not found: $launcherPath"
}
if (-not (Test-Path -LiteralPath $pythonwPath -PathType Leaf)) {
    throw "Ato virtual environment is missing. Complete README setup first."
}
if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
    throw "Shortcut destination does not exist: $Destination"
}
if (Test-Path -LiteralPath $shortcutPath) {
    throw "A shortcut already exists at $shortcutPath. Remove it manually before reinstalling."
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$pythonwPath,0"
$shortcut.Description = 'Launch the Ato personal AI agent'
$shortcut.Save()

Write-Output "Created Ato shortcut: $shortcutPath"
