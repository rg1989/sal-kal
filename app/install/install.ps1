# Sal-Kal on your own computer (Windows 10 and 11). Installs it, or updates it in place (your carts and prices stay),
# then opens the setup page in your browser. No admin rights needed; everything goes in %LOCALAPPDATA%\Sal-Kal.
# Run (PowerShell):  irm https://sal-kal.com/install.ps1 | iex      or double-click Sal-Kal-Setup.cmd
# (SAL_HOME picks another folder; SAL_KAL_SOURCE a .zip URL or a folder of the code, for testing.)
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower while it draws a progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$UvVersion = '0.13.0'
$Src = if ($env:SAL_KAL_SOURCE) { $env:SAL_KAL_SOURCE } else { 'https://github.com/rg1989/sal-kal/archive/refs/heads/main.zip' }
$Root = if ($env:SAL_HOME) { $env:SAL_HOME } else { Join-Path $env:LOCALAPPDATA 'Sal-Kal' }

function Step($n, $text) { Write-Host ''; Write-Host "[$n/5] $text" -ForegroundColor Yellow }
function Fail($text) {
  Write-Host ''
  Write-Host "Sal-Kal could not be installed: $text" -ForegroundColor Red
  throw 'stopped'
}
function Run($exe) {   # a program's own errors stop the install too
  & $exe @args
  if ($LASTEXITCODE -ne 0) { Fail "a step failed (above). Check the internet connection and try again." }
}

Write-Host ''
Write-Host 'Sal-Kal' -ForegroundColor White
Write-Host "Installing into $Root. This takes a few minutes; keep this window open." -ForegroundColor DarkGray
New-Item -ItemType Directory -Force -Path "$Root\bin", "$Root\data" | Out-Null
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("sal-kal-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
$env:UV_PYTHON_INSTALL_DIR = "$Root\python"; $env:UV_CACHE_DIR = "$Root\cache"
$env:UV_NO_CONFIG = '1'; $env:UV_PYTHON_PREFERENCE = 'only-managed'
try {
  Step 1 'Installer tools'
  $Uv = "$Root\bin\uv.exe"
  $have = if (Test-Path $Uv) { (& $Uv --version) -replace '^uv (\S+).*$', '$1' } else { '' }
  if ($have -ne $UvVersion) {
    $target = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
    $u = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$target.zip"
    Invoke-WebRequest $u -OutFile "$Tmp\uv.zip" -UseBasicParsing
    $want = ((Invoke-WebRequest "$u.sha256" -UseBasicParsing).Content -as [string]).Trim().Split(' ')[0]
    if ((Get-FileHash "$Tmp\uv.zip" -Algorithm SHA256).Hash -ne $want.ToUpper()) { Fail "the download of uv didn't match its checksum" }
    Expand-Archive "$Tmp\uv.zip" -DestinationPath "$Tmp\uv" -Force
    Copy-Item (Get-ChildItem "$Tmp\uv" -Recurse -Filter uv.exe | Select-Object -First 1).FullName $Uv -Force
  }

  Step 2 'Python'
  Run $Uv python install -q 3.12

  Step 3 'Sal-Kal'
  $Py = "$Root\venv\Scripts\python.exe"
  if ((Test-Path $Py) -and (Test-Path "$Root\app\app\local.py")) {   # an earlier copy that's running lets go of its files
    Push-Location "$Root\app"; try { & $Py -m app.local stop 2>$null | Out-Null } catch {} finally { Pop-Location }
  }
  $New = "$Root\app.new"
  if (Test-Path $New) { Remove-Item $New -Recurse -Force }
  if (Test-Path $Src -PathType Container) {
    New-Item -ItemType Directory -Force -Path $New | Out-Null
    Get-ChildItem $Src -Force | Where-Object { $_.Name -notin '.git', '.venv', '.claude', '__pycache__' } | Copy-Item -Destination $New -Recurse -Force
  } else {
    Invoke-WebRequest $Src -OutFile "$Tmp\app.zip" -UseBasicParsing
    Expand-Archive "$Tmp\app.zip" -DestinationPath "$Tmp\app" -Force
    $inner = Get-ChildItem "$Tmp\app" -Directory | Select-Object -First 1
    Move-Item $inner.FullName $New
  }
  if (-not (Test-Path "$New\app\local.py")) { Fail "the download doesn't look like Sal-Kal" }
  if (Test-Path "$Root\app") { Remove-Item "$Root\app" -Recurse -Force }
  Move-Item $New "$Root\app"

  Step 4 'Components (about 100 MB)'
  if (-not (Test-Path $Py)) { Run $Uv venv -q --python 3.12 "$Root\venv" }
  Run $Uv pip install -q --python $Py -r "$Root\app\requirements.txt" -r "$Root\app\app\install\requirements.txt"

  Remove-Item "$Root\cache" -Recurse -Force -ErrorAction SilentlyContinue   # the download cache: 200+ MB nothing needs again

  Step 5 'Starting'
  Push-Location "$Root\app"
  try { & $Py -m app.local install; if ($LASTEXITCODE -ne 0) { Fail "it didn't start. Its log: $Root\data\sal-kal.log" } }
  finally { Pop-Location }
  Write-Host 'The setup page opened in your browser. You can close this window.' -ForegroundColor White
  Write-Host ''
} finally {
  Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}
}
