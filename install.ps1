<#
    RimStudio installer.

    One command from PowerShell:
        irm https://raw.githubusercontent.com/AbdulazizJHK/RimStudio/main/install.ps1 | iex

    Or, from a folder you already downloaded:
        powershell -ExecutionPolicy Bypass -File install.ps1

    What it does, in order:
      1. finds the tool files (downloads them if you ran the one-liner)
      2. finds a Python 3.9+, and offers to install one if there is none
      3. builds a private virtual environment with numpy and Pillow in it,
         OUTSIDE the tool folder so it is never committed or cloud-synced
      4. writes %APPDATA%\RimStudio\config.txt so Photoshop can find both
      5. copies the menu stub into every Photoshop it can see (one UAC prompt)

    Nothing here is required to use the tool - it is the manual steps in the
    README, done for you. Undo it all with:  install.ps1 -Uninstall
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    # Remove the menu entry, the virtual environment and the config file.
    [switch]$Uninstall,
    # Skip the Photoshop menu entry (and its UAC prompt).
    [switch]$NoMenu,
    # Use the Python you already have instead of building a virtual environment.
    [switch]$NoVenv,
    # Where to put the tool when it has to be downloaded.
    [string]$InstallTo
)

$ErrorActionPreference = 'Stop'

$REPO       = 'AbdulazizJHK/RimStudio'
$BRANCH     = 'main'
$CFG_DIR    = Join-Path $env:APPDATA 'RimStudio'
$CFG_FILE   = Join-Path $CFG_DIR 'config.txt'
$VENV_DIR   = Join-Path $env:LOCALAPPDATA 'RimStudio\venv'
$MIN_PY     = [version]'3.9'
$MARKER     = 'rimstudio_gui.py'      # the file that proves a folder is the tool

function Say  ($m) { Write-Host "  $m" }
function Step ($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "  $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "`n$m`n" -ForegroundColor Red; exit 1 }


# ---------------------------------------------------------------- Photoshop --
# Every Photoshop on the machine, not just the newest: someone running 2024 and
# 2026 side by side wants the menu entry in both.
function Get-PhotoshopScriptFolders {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    $out = @()
    foreach ($r in $roots) {
        $adobe = Join-Path $r 'Adobe'
        if (-not (Test-Path $adobe)) { continue }
        foreach ($d in Get-ChildItem $adobe -Directory -ErrorAction SilentlyContinue) {
            if ($d.Name -notlike 'Adobe Photoshop*') { continue }
            $s = Join-Path $d.FullName 'Presets\Scripts'
            if (Test-Path $s) { $out += $s }
        }
    }
    # Photoshop installed somewhere non-standard: ask the registry where it is.
    $keys = @('HKLM:\SOFTWARE\Adobe\Photoshop', 'HKLM:\SOFTWARE\WOW6432Node\Adobe\Photoshop')
    foreach ($k in $keys) {
        if (-not (Test-Path $k)) { continue }
        foreach ($v in Get-ChildItem $k -ErrorAction SilentlyContinue) {
            $p = (Get-ItemProperty $v.PSPath -ErrorAction SilentlyContinue).ApplicationPath
            if (-not $p) { continue }
            $s = Join-Path $p 'Presets\Scripts'
            if (Test-Path $s) { $out += $s }
        }
    }
    $out | Sort-Object -Unique
}


# -------------------------------------------------------------- uninstalling --
if ($Uninstall) {
    Step 'Removing RimStudio'

    $folders = Get-PhotoshopScriptFolders
    $stubs = @()
    foreach ($f in $folders) {
        $p = Join-Path $f 'RimStudio.jsx'
        if (Test-Path $p) { $stubs += $p }
    }
    if ($stubs.Count) {
        $lines = $stubs | ForEach-Object { "Remove-Item -LiteralPath '$($_ -replace "'","''")' -Force -ErrorAction SilentlyContinue" }
        $tmp = Join-Path $env:TEMP 'rimstudio_uninstall_elevated.ps1'
        Set-Content -LiteralPath $tmp -Value ($lines -join "`r`n") -Encoding UTF8
        Say 'Removing the Photoshop menu entry (this needs one admin prompt)...'
        Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$tmp`""
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        foreach ($p in $stubs) {
            if (Test-Path $p) { Warn "Still there: $p" } else { Good "Removed $p" }
        }
    } else {
        Say 'No menu entry found.'
    }

    if (Test-Path $VENV_DIR) { Remove-Item $VENV_DIR -Recurse -Force; Good "Removed $VENV_DIR" }
    if (Test-Path $CFG_DIR)  { Remove-Item $CFG_DIR  -Recurse -Force; Good "Removed $CFG_DIR" }

    Write-Host "`nDone. The tool folder itself was left alone - delete it if you want it gone.`n"
    exit 0
}


Write-Host ''
Write-Host '  RimStudio' -ForegroundColor White
Write-Host '  Composite a cut-out into a plate, and make it look photographed there.'


# ------------------------------------------------------- 1. find the sources --
Step '[1/5] Locating the tool files'

$tool = $null
# $PSScriptRoot is empty when this is piped from the web (irm | iex).
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot $MARKER))) {
    $tool = (Resolve-Path $PSScriptRoot).Path
    Say "Using the folder this installer is in:"
    Say "  $tool"
} elseif ((Test-Path (Join-Path (Get-Location) $MARKER))) {
    $tool = (Resolve-Path (Get-Location)).Path
    Say "Using the current folder:"
    Say "  $tool"
} else {
    $tool = if ($InstallTo) { $InstallTo } else { Join-Path $env:LOCALAPPDATA 'Programs\RimStudio' }
    Say "Downloading $REPO ($BRANCH) into:"
    Say "  $tool"
    $zip = Join-Path $env:TEMP 'rimstudio_download.zip'
    $out = Join-Path $env:TEMP 'rimstudio_download'
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest "https://github.com/$REPO/archive/refs/heads/$BRANCH.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -LiteralPath $zip -DestinationPath $out -Force
    } catch {
        Die "Could not download the tool: $($_.Exception.Message)`n  Download the ZIP from https://github.com/$REPO yourself, unzip it, and run install.ps1 inside it."
    }
    $src = Get-ChildItem $out -Directory | Select-Object -First 1
    if (-not $src) { Die 'The downloaded archive was empty.' }
    New-Item -ItemType Directory -Force -Path $tool | Out-Null
    Copy-Item (Join-Path $src.FullName '*') $tool -Recurse -Force
    Remove-Item $zip, $out -Recurse -Force -ErrorAction SilentlyContinue
    Good 'Downloaded.'
}

if (-not (Test-Path (Join-Path $tool $MARKER))) {
    Die "That folder has no $MARKER in it, so it is not the tool."
}


# ------------------------------------------------------------ 2. find Python --
Step '[2/5] Looking for Python 3.9 or newer'

function Test-PythonExe ($exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
    try {
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $v) { return $null }
    try { $ver = [version]$v.Trim() } catch { return $null }
    if ($ver -lt $MIN_PY) { return $null }
    [pscustomobject]@{ Exe = (Resolve-Path -LiteralPath $exe).Path; Version = $ver }
}

function Find-Python {
    $seen = @()
    # The py launcher knows about every Python on the machine, so ask it first.
    $pyl = Join-Path $env:WINDIR 'py.exe'
    if (Test-Path $pyl) {
        foreach ($flag in '-3.13','-3.12','-3.11','-3') {
            try { $p = & $pyl $flag -c "import sys; print(sys.executable)" 2>$null } catch { $p = $null }
            if ($p) { $seen += $p.Trim() }
        }
    }
    foreach ($n in 'python.exe') {
        foreach ($c in (Get-Command $n -All -ErrorAction SilentlyContinue)) { $seen += $c.Source }
    }
    foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles", 'C:\')) {
        if (-not (Test-Path $base)) { continue }
        foreach ($d in Get-ChildItem $base -Directory -Filter 'Python*' -ErrorAction SilentlyContinue) {
            $seen += (Join-Path $d.FullName 'python.exe')
        }
    }
    foreach ($e in $seen) {
        # The Store stub in WindowsApps is a 0-byte redirector that opens the
        # Store instead of running anything - it answers -c with nothing.
        if ($e -like '*\WindowsApps\*') { continue }
        $ok = Test-PythonExe $e
        if ($ok) { return $ok }
    }
    $null
}

$py = Find-Python
if (-not $py) {
    Warn 'No suitable Python found.'
    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if ($wg) {
        Say 'Installing Python 3.12 with winget (this can take a couple of minutes)...'
        & winget install --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
        $env:PATH = [Environment]::GetEnvironmentVariable('PATH','User') + ';' + [Environment]::GetEnvironmentVariable('PATH','Machine')
        $py = Find-Python
    }
    if (-not $py) {
        Die "Install Python from https://www.python.org/downloads/windows/ (tick `"Add python.exe to PATH`"), then run this installer again."
    }
}
Good "Python $($py.Version) at $($py.Exe)"


# -------------------------------------------------- 3. dependencies in a venv --
Step '[3/5] Installing numpy and Pillow'

$runtime = $py.Exe
$runtimeW = Join-Path (Split-Path $py.Exe) 'pythonw.exe'

if ($NoVenv) {
    Say 'Using your own Python (-NoVenv), installing into it with --user...'
    & $py.Exe -m pip install --user --upgrade --disable-pip-version-check numpy Pillow
    if ($LASTEXITCODE -ne 0) { Die 'pip failed. Run the same command by hand to see why.' }
} else {
    if (Test-Path $VENV_DIR) {
        Say 'Reusing the existing environment.'
    } else {
        Say "Building a private environment in $VENV_DIR"
        Say '(kept out of the tool folder so it is never committed or cloud-synced)'
        & $py.Exe -m venv "$VENV_DIR"
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $VENV_DIR 'Scripts\python.exe'))) {
            Warn 'Could not build the environment; falling back to your own Python.'
            Remove-Item $VENV_DIR -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path (Join-Path $VENV_DIR 'Scripts\python.exe')) {
        $runtime  = Join-Path $VENV_DIR 'Scripts\python.exe'
        $runtimeW = Join-Path $VENV_DIR 'Scripts\pythonw.exe'
    }
    $req = Join-Path $tool 'requirements.txt'
    if (Test-Path $req) { & $runtime -m pip install --upgrade --disable-pip-version-check -r "$req" }
    else                { & $runtime -m pip install --upgrade --disable-pip-version-check numpy Pillow }
    if ($LASTEXITCODE -ne 0) { Die 'pip failed. Check your internet connection or any proxy, then run this again.' }
}

# tkinter is what draws the window. It ships with python.org builds but some
# rebuilds and most conda ones leave it out, and the failure is invisible
# because the panel launches with pythonw (no console to print to).
$check = & $runtime -c "import numpy, PIL, tkinter; print(numpy.__version__, PIL.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Die "The environment is not usable:`n$check`n  If tkinter is the missing one, reinstall Python from python.org and keep the `"tcl/tk and IDLE`" option ticked."
}
Good "numpy + Pillow + tkinter OK ($check)"
if (-not (Test-Path $runtimeW)) {
    Warn 'No pythonw.exe beside that Python - a console window will flash on launch.'
    $runtimeW = $runtime
}


# --------------------------------------------------------- 4. write the config --
Step '[4/5] Recording where everything is'

New-Item -ItemType Directory -Force -Path $CFG_DIR | Out-Null
$cfgText = @(
    '# Written by install.ps1. Photoshop reads this to find the tool.'
    "tool=$tool"
    "pythonw=$runtimeW"
    "python=$runtime"
) -join "`r`n"
# UTF-8 with no BOM, written explicitly: Set-Content -Encoding UTF8 adds a BOM
# on Windows PowerShell 5.1, and ExtendScript would read it as stray characters.
[IO.File]::WriteAllText($CFG_FILE, $cfgText + "`r`n", (New-Object Text.UTF8Encoding($false)))
Good "$CFG_FILE"


# ------------------------------------------------------ 5. the Photoshop menu --
Step '[5/5] Adding it to Photoshop'

if ($NoMenu) {
    Say 'Skipped (-NoMenu). Use File > Scripts > Browse... and pick "RimStudio Panel.jsx".'
} else {
    $targets = Get-PhotoshopScriptFolders
    if (-not $targets) {
        Warn 'No Photoshop found in Program Files.'
        Say  'The tool still works standalone, and inside Photoshop via File > Scripts > Browse...'
    } else {
        foreach ($t in $targets) { Say "Found: $t" }
        $stub = Join-Path $tool 'RimStudio.jsx'
        if (-not (Test-Path $stub)) { Die "RimStudio.jsx is missing from $tool" }

        # Program Files needs admin, and only this one copy does - so the prompt
        # comes here, at the end, rather than gating the whole install.
        $lines = @("`$ErrorActionPreference='Stop'")
        foreach ($t in $targets) {
            $d = ($t -replace "'","''")
            $s = ($stub -replace "'","''")
            $lines += "Copy-Item -LiteralPath '$s' -Destination '$d' -Force"
        }
        $tmp = Join-Path $env:TEMP 'rimstudio_install_elevated.ps1'
        Set-Content -LiteralPath $tmp -Value ($lines -join "`r`n") -Encoding UTF8
        Say 'Copying the menu entry into Program Files (one admin prompt)...'
        try {
            $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$tmp`""
        } catch {
            $p = $null
            Warn 'The admin prompt was declined.'
        }
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue

        $ok = 0
        foreach ($t in $targets) { if (Test-Path (Join-Path $t 'RimStudio.jsx')) { $ok++ } }
        if ($ok) { Good "Installed into $ok Photoshop install$(if($ok -ne 1){'s'})." }
        else {
            Warn 'Could not write into Program Files.'
            Say  "Copy `"$stub`" into one of the folders above yourself, or just use File > Scripts > Browse..."
        }
    }
}


Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host ''
Write-Host '  Restart Photoshop, then: File > Scripts > RimStudio'
Write-Host '  Select your cut-out layer first, then press "Pull from Photoshop".'
Write-Host ''
Write-Host '  Without Photoshop:'
Write-Host "    `"$runtime`" `"$(Join-Path $tool 'rimstudio_gui.py')`" subject.png background.png"
Write-Host ''
