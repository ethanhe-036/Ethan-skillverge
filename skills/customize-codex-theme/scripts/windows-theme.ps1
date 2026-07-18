[CmdletBinding()]
param(
  [ValidateSet('doctor', 'prepare', 'start', 'verify', 'switch', 'restore', 'status')]
  [string]$Action = 'status',
  [string]$Theme,
  [ValidateRange(1024, 65535)]
  [int]$Port = 9341,
  [switch]$CreateLaunchers,
  [switch]$AuthorizedRestart
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = $PSScriptRoot
$SkillRoot = Split-Path -Parent $ScriptRoot
$Catalog = Join-Path $SkillRoot 'assets\presets'
$ThemeTool = Join-Path $ScriptRoot 'theme-tool.mjs'
$Injector = Join-Path $ScriptRoot 'injector.mjs'
$StateRoot = Join-Path $env:LOCALAPPDATA 'CodexThemeStudio'
$StatePath = Join-Path $StateRoot 'theme-state.json'
$RuntimePath = Join-Path $StateRoot 'runtime.json'
$StdoutPath = Join-Path $StateRoot 'injector.log'
$StderrPath = Join-Path $StateRoot 'injector-error.log'

function Get-CodexInstall {
  $package = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue |
    Sort-Object Version -Descending | Select-Object -First 1
  if (-not $package) { throw 'The official OpenAI.Codex Store package is not installed.' }
  if ([string]$package.SignatureKind -eq 'None') { throw 'The OpenAI.Codex package is not signed.' }

  $exe = Join-Path $package.InstallLocation 'app\ChatGPT.exe'
  if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Codex executable not found: $exe" }
  $root = [IO.Path]::GetFullPath($package.InstallLocation).TrimEnd('\') + '\'
  $resolved = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $exe).Path)
  if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Codex executable resolved outside its signed package directory.'
  }
  $manifest = Get-AppxPackageManifest -Package $package.PackageFullName
  $application = @($manifest.Package.Applications.Application) |
    Where-Object { [string]$_.Executable -match '(?:^|[/\\])ChatGPT\.exe$' } |
    Select-Object -First 1
  if (-not $application.Id) { throw 'Could not resolve the stable Codex application ID.' }
  [pscustomobject]@{
    Package = $package
    Exe = $resolved
    Root = $root.TrimEnd('\')
    AppId = "$($package.PackageFamilyName)!$($application.Id)"
  }
}

function Get-Node([object]$CodexInstall = $null, [switch]$AllowMissing) {
  $candidates = [Collections.Generic.List[object]]::new()
  if ($env:CODEX_THEME_NODE) {
    $candidates.Add([pscustomobject]@{ Path = $env:CODEX_THEME_NODE; Source = 'CODEX_THEME_NODE' })
  }
  if ($CodexInstall) {
    $candidates.Add([pscustomobject]@{ Path = (Join-Path $CodexInstall.Root 'app\resources\cua_node\bin\node.exe'); Source = 'Codex bundled' })
    $candidates.Add([pscustomobject]@{ Path = (Join-Path $CodexInstall.Root 'app\resources\node.exe'); Source = 'Codex bundled' })
  }
  $pathNode = Get-Command node -ErrorAction SilentlyContinue
  if ($pathNode) { $candidates.Add([pscustomobject]@{ Path = $pathNode.Source; Source = 'PATH' }) }

  foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate.Path -PathType Leaf)) { continue }
    try {
      $version = (& $candidate.Path --version 2>$null | Select-Object -First 1)
      if ($LASTEXITCODE -ne 0 -or $version -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 20) { continue }
      & $candidate.Path -e 'if(typeof globalThis.WebSocket!=="function")process.exit(1)' 2>$null
      if ($LASTEXITCODE -ne 0) { continue }
      return [pscustomobject]@{
        Path = (Resolve-Path -LiteralPath $candidate.Path).Path
        Version = $version
        Source = $candidate.Source
        Status = 'READY'
        WebSocket = $true
      }
    } catch { continue }
  }
  if ($AllowMissing) { return $null }
  throw 'A Node.js 20+ runtime with built-in WebSocket support is required. Set CODEX_THEME_NODE to a trusted runtime.'
}

function Invoke-NodeJson([string]$Node, [string]$Program, [string[]]$Arguments) {
  $output = & $Node $Program @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program $($Arguments -join ' ')" }
  try { return ($output | Out-String | ConvertFrom-Json) }
  catch { throw "Command returned invalid JSON: $Program" }
}

function Get-ThemeStatus([string]$Node) {
  Invoke-NodeJson $Node $ThemeTool @('status', '--state', $StatePath)
}

function Resolve-SelectedTheme([string]$Node, [switch]$PreferLoaded) {
  $status = Get-ThemeStatus $Node
  $id = if ($PreferLoaded -and $status.loadedTheme) {
    $status.loadedTheme
  } elseif ($status.nextLaunchTheme) {
    $status.nextLaunchTheme
  } else {
    $status.selectedTheme
  }
  if (-not $id) { throw 'No theme is selected. Run prepare or switch with -Theme first.' }
  Invoke-NodeJson $Node $ThemeTool @('resolve', '--catalog', $Catalog, '--theme', [string]$id)
}

function Get-CodexProcesses([object]$Install) {
  @(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -and ([IO.Path]::GetFullPath($_.Path) -eq $Install.Exe) } catch { $false }
  })
}

function Get-DesktopLauncherStatus {
  $desktop = [Environment]::GetFolderPath('Desktop')
  if (-not $desktop) { return @() }
  $shell = New-Object -ComObject WScript.Shell
  @('Codex Themes.lnk', 'Codex Original.lnk') | ForEach-Object {
    $path = Join-Path $desktop $_
    if (Test-Path -LiteralPath $path -PathType Leaf) {
      $shortcut = $shell.CreateShortcut($path)
      $iconPath = ([string]$shortcut.IconLocation -replace ',\s*-?\d+$', '').Trim('"')
      [pscustomobject]@{
        path = $path
        owner = (Get-Acl -LiteralPath $path).Owner
        target = $shortcut.TargetPath
        arguments = $shortcut.Arguments
        workingDirectory = $shortcut.WorkingDirectory
        iconLocation = $shortcut.IconLocation
        targetExists = [bool](Test-Path -LiteralPath $shortcut.TargetPath -PathType Leaf)
        iconExists = [bool]($iconPath -and (Test-Path -LiteralPath $iconPath -PathType Leaf))
      }
    }
  }
}

function Test-VerifiedCdp([int]$CandidatePort, [object]$Install) {
  try {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $CandidatePort -ErrorAction Stop)
    if ($listeners.Count -eq 0 -or @($listeners | Where-Object LocalAddress -ne '127.0.0.1').Count -gt 0) {
      return $false
    }
    foreach ($listener in $listeners) {
      $owner = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
      if (-not $owner.Path -or ([IO.Path]::GetFullPath($owner.Path) -ne $Install.Exe)) { return $false }
    }
    $targets = @(Invoke-RestMethod "http://127.0.0.1:$CandidatePort/json/list" -TimeoutSec 1)
    return @($targets | Where-Object { $_.type -eq 'page' -and $_.url -like 'app://*' }).Count -gt 0
  } catch {
    return $false
  }
}

function Stop-RecordedInjector {
  if (-not (Test-Path -LiteralPath $RuntimePath -PathType Leaf)) { return }
  try { $runtime = Get-Content -LiteralPath $RuntimePath -Raw | ConvertFrom-Json }
  catch { throw "Could not parse recorded injector state; refusing unsafe cleanup: $($_.Exception.Message)" }
  if (-not $runtime.injectorPid -or -not $runtime.nodePath -or -not $runtime.injectorPath -or -not $runtime.injectorStartedAt) {
    throw 'Recorded injector state is incomplete; refusing unsafe cleanup.'
  }
  $process = Get-Process -Id ([int]$runtime.injectorPid) -ErrorAction SilentlyContinue
  if (-not $process) { return }
  try { $command = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$runtime.injectorPid)" -ErrorAction Stop }
  catch { throw "Could not verify recorded injector PID $($runtime.injectorPid); refusing to stop it." }
  $sameNode = $process.Path -and
    ([IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath([string]$runtime.nodePath))
  $sameStart = $process.StartTime.ToUniversalTime().ToString('o') -eq [string]$runtime.injectorStartedAt
  $sameCommand = $command.CommandLine -and
    $command.CommandLine.IndexOf([string]$runtime.injectorPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $command.CommandLine -match '(?:^|\s)watch(?:\s|$)'
  if (-not ($sameNode -and $sameStart -and $sameCommand)) {
    throw "PID $($runtime.injectorPid) no longer matches the recorded theme watcher; refusing to stop it."
  }
  Stop-Process -Id $process.Id -ErrorAction Stop
  [void]$process.WaitForExit(3000)
}

function Write-Runtime([Diagnostics.Process]$Process, [string]$Node, [object]$ResolvedTheme) {
  New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
  $record = [ordered]@{
    schemaVersion = 1
    port = $Port
    injectorPid = $Process.Id
    injectorStartedAt = $Process.StartTime.ToUniversalTime().ToString('o')
    injectorPath = (Resolve-Path -LiteralPath $Injector).Path
    nodePath = (Resolve-Path -LiteralPath $Node).Path
    themeId = $ResolvedTheme.id
    themeDir = $ResolvedTheme.directory
    themeHash = $ResolvedTheme.hash
  }
  $temporary = "$RuntimePath.$PID.tmp"
  $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
  Move-Item -LiteralPath $temporary -Destination $RuntimePath -Force
}

function New-DesktopLaunchers([object]$Install) {
  $desktop = [Environment]::GetFolderPath('Desktop')
  if (-not $desktop) { throw 'Could not resolve the Desktop directory.' }
  $shell = New-Object -ComObject WScript.Shell
  $powerShell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $powerShell) { $powerShell = (Get-Process -Id $PID).Path }

  $themesPath = Join-Path $desktop 'Codex Themes.lnk'
  $originalPath = Join-Path $desktop 'Codex Original.lnk'
  if ((Test-Path -LiteralPath $themesPath) -or (Test-Path -LiteralPath $originalPath)) {
    throw 'A Codex Themes or Codex Original desktop shortcut already exists; refusing to overwrite it.'
  }
  $iconSource = Join-Path $Install.Root 'app\resources\icon-chatgpt.ico'
  if (-not (Test-Path -LiteralPath $iconSource -PathType Leaf)) {
    throw "Official Codex icon not found: $iconSource"
  }
  New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
  $stableIcon = Join-Path $StateRoot 'codex.ico'
  Copy-Item -LiteralPath $iconSource -Destination $stableIcon -Force

  $themes = $shell.CreateShortcut($themesPath)
  $themes.TargetPath = $powerShell
  $themes.Arguments = "-NoProfile -File `"$PSCommandPath`" -Action start -Port $Port -AuthorizedRestart"
  $themes.WorkingDirectory = $ScriptRoot
  $themes.IconLocation = "$stableIcon,0"
  $themes.Save()

  $original = $shell.CreateShortcut($originalPath)
  $original.TargetPath = (Get-Command explorer.exe -ErrorAction Stop).Source
  $original.Arguments = "shell:AppsFolder\$($Install.AppId)"
  $original.WorkingDirectory = $desktop
  $original.IconLocation = "$stableIcon,0"
  $original.Save()
  Write-Host 'Created Codex Themes and Codex Original. Fully exit Codex before using Original.'
}

$install = $null
$nodeInfo = $null

switch ($Action) {
  'doctor' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install -AllowMissing
    if (-not $nodeInfo) {
      [pscustomobject]@{
        pass = $false
        status = 'NOT_READY'
        platform = 'Windows'
        codexVersion = [string]$install.Package.Version
        codexPath = $install.Exe
        nodeVersion = $null
        nodeRuntime = [ordered]@{
          status = 'NOT_READY'
          required = 'Node.js 20+ with built-in WebSocket'
          source = $null
          path = $null
          version = $null
          webSocket = $false
          reason = 'No trusted compatible Node.js runtime was found.'
        }
        stateRoot = $StateRoot
        runningPids = @((Get-CodexProcesses $install) | ForEach-Object Id)
        verifiedCdp = [bool](Test-VerifiedCdp $Port $install)
        themeState = $null
        launchers = @(Get-DesktopLauncherStatus)
      } | ConvertTo-Json -Depth 6
      break
    }
    $catalogResult = Invoke-NodeJson $nodeInfo.Path $ThemeTool @('validate', '--catalog', $Catalog)
    $themeState = Get-ThemeStatus $nodeInfo.Path
    [pscustomobject]@{
      pass = [bool]$catalogResult.pass
      status = 'READY'
      platform = 'Windows'
      codexVersion = [string]$install.Package.Version
      codexPath = $install.Exe
      nodeVersion = $nodeInfo.Version
      nodeRuntime = [ordered]@{
        status = $nodeInfo.Status
        required = 'Node.js 20+ with built-in WebSocket'
        source = $nodeInfo.Source
        path = $nodeInfo.Path
        version = $nodeInfo.Version
        webSocket = $nodeInfo.WebSocket
      }
      stateRoot = $StateRoot
      runningPids = @((Get-CodexProcesses $install) | ForEach-Object Id)
      verifiedCdp = [bool](Test-VerifiedCdp $Port $install)
      themeState = $themeState
      launchers = @(Get-DesktopLauncherStatus)
    } | ConvertTo-Json -Depth 6
  }

  { $_ -in @('prepare', 'switch') } {
    if (-not $Theme) { throw "$Action requires -Theme <id>." }
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    & $nodeInfo.Path $ThemeTool select --catalog $Catalog --state $StatePath --theme $Theme
    if ($LASTEXITCODE -ne 0) { throw "Could not select theme: $Theme" }
    if ($CreateLaunchers) { New-DesktopLaunchers $install }
  }

  'start' {
    if (-not $AuthorizedRestart) { throw 'start requires -AuthorizedRestart after explicit current-turn authorization, or a deliberate Codex Themes launcher click.' }
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value.Replace('-', '_')
    $startMutex = [Threading.Mutex]::new($false, "Global\CodexThemeStudio.Start.$userSid")
    $startLockTaken = $false
    try {
      try { $startLockTaken = $startMutex.WaitOne(0) }
      catch [Threading.AbandonedMutexException] { $startLockTaken = $true }
      if (-not $startLockTaken) { throw 'Another Codex Themes start is already in progress; refusing a duplicate launch.' }
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    $resolved = Resolve-SelectedTheme $nodeInfo.Path
    & $nodeInfo.Path $Injector check --theme-dir $resolved.directory
    if ($LASTEXITCODE -ne 0) { throw 'Theme payload validation failed.' }

    $running = Get-CodexProcesses $install
    if ($running.Count -gt 0) {
      throw 'Codex is already running. Theme changes are next-launch only; fully exit Codex manually, then run start again.'
    }
    $cdpReady = $false
    if (-not $cdpReady) {
      if (@(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).Count -gt 0) {
        throw "Port $Port is already in use; refusing to attach to an unknown listener."
      }
      Start-Process -FilePath $install.Exe -ArgumentList @(
        '--remote-debugging-address=127.0.0.1', "--remote-debugging-port=$Port"
      ) | Out-Null
      $deadline = (Get-Date).AddSeconds(35)
      while (-not (Test-VerifiedCdp $Port $install)) {
        if ((Get-Date) -ge $deadline) { throw "Codex did not expose a verified loopback endpoint on port $Port." }
        Start-Sleep -Milliseconds 400
      }
    }

    Stop-RecordedInjector
    & $nodeInfo.Path $Injector once --theme-dir $resolved.directory --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Theme injection failed; the native interface was kept.' }
    & $nodeInfo.Path $Injector verify --theme-dir $resolved.directory --port $Port
    if ($LASTEXITCODE -ne 0) {
      & $nodeInfo.Path $Injector remove --theme-dir $resolved.directory --port $Port *> $null
      throw 'Theme verification failed; the native interface was restored.'
    }

    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    $quotedInjector = '"' + $Injector + '"'
    $quotedTheme = '"' + [string]$resolved.directory + '"'
    $watcher = Start-Process -FilePath $nodeInfo.Path -ArgumentList @(
      $quotedInjector, 'watch', '--theme-dir', $quotedTheme, '--port', "$Port"
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
    Start-Sleep -Milliseconds 350
    if ($watcher.HasExited) { throw "Theme watcher exited during startup. See $StderrPath" }
    Write-Runtime $watcher $nodeInfo.Path $resolved
    & $nodeInfo.Path $ThemeTool mark-loaded --state $StatePath --theme $resolved.id --hash $resolved.hash
    if ($LASTEXITCODE -ne 0) { Stop-RecordedInjector; throw 'Could not record the loaded theme.' }
    } finally {
      if ($startLockTaken) { $startMutex.ReleaseMutex() }
      $startMutex.Dispose()
    }
  }

  'verify' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    if (-not (Test-VerifiedCdp $Port $install)) { throw 'No verified Codex loopback endpoint is available.' }
    $resolved = Resolve-SelectedTheme $nodeInfo.Path -PreferLoaded
    & $nodeInfo.Path $Injector verify --theme-dir $resolved.directory --port $Port
    exit $LASTEXITCODE
  }

  'restore' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    Stop-RecordedInjector
    if (Test-VerifiedCdp $Port $install) {
      & $nodeInfo.Path $Injector remove --port $Port
      if ($LASTEXITCODE -ne 0) { throw 'Could not remove the injected theme safely.' }
    }
    & $nodeInfo.Path $ThemeTool mark-restored --state $StatePath
    if ($LASTEXITCODE -ne 0) { throw 'Could not record restored state.' }
    Remove-Item -LiteralPath $RuntimePath -Force -ErrorAction SilentlyContinue
  }

  'status' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    & $nodeInfo.Path $ThemeTool status --state $StatePath
    exit $LASTEXITCODE
  }
}
