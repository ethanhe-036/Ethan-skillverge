[CmdletBinding()]
param(
  [ValidateSet('doctor', 'compatibility-audit', 'prepare', 'start', 'verify', 'refresh', 'switch', 'rollback', 'restore', 'status', 'migrate')]
  [string]$Action = 'status',
  [string]$Theme,
  [ValidateRange(1024, 65535)]
  [int]$Port = 9341,
  [switch]$CreateLaunchers,
  [switch]$ShowThemeSelector,
  [switch]$AuthorizedRestart
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = $PSScriptRoot
$SkillRoot = Split-Path -Parent $ScriptRoot
$Catalog = Join-Path $SkillRoot 'assets\presets'
$ThemeTool = Join-Path $ScriptRoot 'theme-tool.mjs'
$Injector = Join-Path $ScriptRoot 'injector.mjs'
$Selector = Join-Path $ScriptRoot 'windows-theme-selector.ps1'
$SkillManifestPath = Join-Path $SkillRoot 'skill.manifest.json'
$StateRoot = Join-Path $env:LOCALAPPDATA 'CodexThemeStudio'
$StatePath = Join-Path $StateRoot 'theme-state.json'
$RuntimePath = Join-Path $StateRoot 'runtime.json'
$StdoutPath = Join-Path $StateRoot 'injector.log'
$StderrPath = Join-Path $StateRoot 'injector-error.log'
$WatchStatusPath = Join-Path $StateRoot 'watch-status.json'
$ColdStartEndpointTimeoutSeconds = 60
$ColdStartRendererTimeoutMilliseconds = 90000
$RefreshRendererTimeoutMilliseconds = 10000
$ThemesLauncherDescription = 'Codex Theme Studio managed launcher; kind=themes; schema=2'
$OriginalLauncherDescription = 'Codex Theme Studio managed launcher; kind=original; schema=2'
$LegacyDreamSkinLauncherName = 'Codex Dream Skin.lnk'
$LegacyDreamSkinDescription = 'Launch Codex with the paper-art voyage skin'
$LegacyDreamSkinTheme = 'one-piece-paper-adventure'

. $Selector

function Get-SkillManifest {
  if (-not (Test-Path -LiteralPath $SkillManifestPath -PathType Leaf)) {
    throw "The canonical skill manifest is missing: $SkillManifestPath"
  }
  try { $manifest = Get-Content -LiteralPath $SkillManifestPath -Raw | ConvertFrom-Json }
  catch { throw "Could not parse the canonical skill manifest: $($_.Exception.Message)" }
  if ([int]$manifest.schemaVersion -ne 2 -or [string]$manifest.name -ne 'customize-codex-theme' -or
      [string]$manifest.canonicalInstall.strategy -ne 'codex-home-skill' -or
      [string]$manifest.canonicalInstall.skillName -ne 'customize-codex-theme' -or
      [string]$manifest.canonicalInstall.environmentOverride -ne 'CODEX_HOME') {
    throw 'The canonical skill manifest has an unsupported identity.'
  }
  $manifest
}

function Get-ExpectedCanonicalSkillRoot([object]$Manifest) {
  $codexHomeRoot = if ([string]$env:CODEX_HOME) {
    [IO.Path]::GetFullPath([string]$env:CODEX_HOME)
  } else {
    [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'))
  }
  Join-Path (Join-Path $codexHomeRoot 'skills') ([string]$Manifest.canonicalInstall.skillName)
}

function Assert-CanonicalSkillRoot {
  $manifest = Get-SkillManifest
  $expectedRoot = Get-ExpectedCanonicalSkillRoot $manifest
  if (-not (Test-SamePath $SkillRoot $expectedRoot)) {
    throw "This is not the canonical customize-codex-theme installation. Expected: $expectedRoot; actual: $SkillRoot"
  }
  $manifest
}

function Get-RuntimeCanonicalStatus {
  if (-not (Test-Path -LiteralPath $RuntimePath -PathType Leaf)) {
    return [pscustomobject]@{ present = $false; canonical = $true; injectorPath = $null; themeDir = $null }
  }
  try { $runtime = Get-Content -LiteralPath $RuntimePath -Raw | ConvertFrom-Json }
  catch {
    return [pscustomobject]@{ present = $true; canonical = $false; injectorPath = $null; themeDir = $null }
  }
  $catalogPrefix = [IO.Path]::GetFullPath($Catalog).TrimEnd('\') + '\'
  $runtimeTheme = try { [IO.Path]::GetFullPath([string]$runtime.themeDir) } catch { '' }
  [pscustomobject]@{
    present = $true
    canonical = [bool](
      (Test-SamePath ([string]$runtime.injectorPath) $Injector) -and
      $runtimeTheme.StartsWith($catalogPrefix, [StringComparison]::OrdinalIgnoreCase)
    )
    injectorPath = [string]$runtime.injectorPath
    themeDir = [string]$runtime.themeDir
  }
}

function Get-WatchStatus {
  if (-not (Test-Path -LiteralPath $WatchStatusPath -PathType Leaf)) {
    return [pscustomobject]@{ present = $false; active = $false }
  }
  try { $status = Get-Content -LiteralPath $WatchStatusPath -Raw | ConvertFrom-Json }
  catch { return [pscustomobject]@{ present = $true; active = $false; error = 'invalid-json' } }
  $active = $false
  if ([int]$status.pid -gt 0) {
    $active = $null -ne (Get-Process -Id ([int]$status.pid) -ErrorAction SilentlyContinue)
  }
  [pscustomobject]@{
    present = $true
    active = $active
    event = [string]$status.event
    pid = [int]$status.pid
    generation = [int]$status.generation
    themeId = [string]$status.themeId
    themeDir = [string]$status.themeDir
    payloadHash = [string]$status.payloadHash
    payloadRevision = [string]$status.payloadRevision
    cssHash = [string]$status.cssHash
    styleRevision = [string]$status.styleRevision
    targetCount = $status.targetCount
    error = $status.error
    updatedAt = [string]$status.updatedAt
  }
}

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

function Assert-TrustedNodeImage([string]$Path) {
  $resolved = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path)
  $signature = Get-AuthenticodeSignature -LiteralPath $resolved -ErrorAction Stop
  if ([string]$signature.Status -ine 'Valid') {
    throw "The Node.js runtime is not validly signed: $resolved ($($signature.Status))."
  }
  $subject = [string]$signature.SignerCertificate.Subject
  if ($subject -notmatch '(?i)O=("?)(OpenJS Foundation|Node\.js Foundation|Microsoft Corporation|GitHub, Inc\.)') {
    throw "The Node.js runtime is signed by an unexpected publisher: $subject"
  }
  [pscustomobject]@{ Path = $resolved; Signer = $subject }
}

function Get-Node([object]$CodexInstall = $null, [switch]$AllowMissing) {
  $candidates = [Collections.Generic.List[object]]::new()
  if ($CodexInstall) {
    $candidates.Add([pscustomobject]@{ Path = (Join-Path $CodexInstall.Root 'app\resources\cua_node\bin\node.exe'); Source = 'Codex bundled' })
    $candidates.Add([pscustomobject]@{ Path = (Join-Path $CodexInstall.Root 'app\resources\node.exe'); Source = 'Codex bundled' })
  }
  $pathNode = Get-Command node -CommandType Application -ErrorAction SilentlyContinue
  if ($pathNode) { $candidates.Add([pscustomobject]@{ Path = $pathNode.Source; Source = 'PATH' }) }

  foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate.Path -PathType Leaf)) { continue }
    try {
      # Authenticity is checked before the candidate can execute any validator or probe.
      $trusted = Assert-TrustedNodeImage -Path ([string]$candidate.Path)
      $nodePath = [string]$trusted.Path
      $versionOutput = & $nodePath --version 2>$null
      $versionExit = $LASTEXITCODE
      $version = [string]@($versionOutput)[0]
      if ($versionExit -ne 0 -or $version -notmatch '^v(\d+)\.' -or [int]$Matches[1] -lt 20) { continue }
      & $nodePath -e 'if(!globalThis.WebSocket)process.exit(1)' 2>$null
      $webSocketExit = $LASTEXITCODE
      if ($webSocketExit -ne 0) { continue }
      return [pscustomobject]@{
        Path = (Resolve-Path -LiteralPath $candidate.Path).Path
        Version = $version
        Source = $candidate.Source
        Status = 'READY'
        WebSocket = $true
        Signer = [string]$trusted.Signer
      }
    } catch { continue }
  }
  if ($AllowMissing) { return $null }
  throw 'A validly signed Node.js 20+ runtime with built-in WebSocket support is required from Codex or PATH.'
}

function Invoke-NodeJson([string]$Node, [string]$Program, [string[]]$Arguments) {
  $previousConsoleEncoding = [Console]::OutputEncoding
  $previousOutputEncoding = $OutputEncoding
  $utf8 = [Text.UTF8Encoding]::new($false)
  try {
    # Windows PowerShell 5.1 has no ProcessStartInfo.ArgumentList. The call
    # operator preserves the argument array without manual command-line
    # quoting, while the local UTF-8 boundary prevents CP936 JSON corruption.
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
    $stdout = @(& $Node $Program @Arguments 2>$null)
    $nativeExitCode = $LASTEXITCODE
    $json = $stdout -join [Environment]::NewLine
    $parsed = $null
    if (-not [string]::IsNullOrWhiteSpace($json)) {
      try { $parsed = $json | ConvertFrom-Json -ErrorAction Stop }
      catch {
        if ($nativeExitCode -eq 0) { throw "Command returned invalid JSON: $Program" }
      }
    }
    if ($nativeExitCode -ne 0) {
      $failureDetail = $null
      if ($parsed -and $parsed.PSObject.Properties['error'] -and [string]$parsed.error) {
        $failureDetail = [string]$parsed.error
      } elseif ($parsed -and $parsed.PSObject.Properties['targets']) {
        $failedTarget = $null
        foreach ($target in @($parsed.targets)) {
          if ($target.PSObject.Properties['result'] -and -not [bool]$target.result.pass) {
            $failedTarget = $target
            break
          }
        }
        if ($failedTarget) {
          $failureParts = @()
          if ($failedTarget.result.PSObject.Properties['textContrast']) {
            $failureParts += "text contrast risks=$([int]$failedTarget.result.textContrast.riskCount)"
          }
          if ($failedTarget.result.PSObject.Properties['semanticTokenContrast']) {
            $failureParts += "semantic color risks=$([int]$failedTarget.result.semanticTokenContrast.riskCount)"
          }
          if ($failedTarget.result.PSObject.Properties['overflowX'] -and [bool]$failedTarget.result.overflowX) {
            $failureParts += 'horizontal overflow=true'
          }
          $failureDetail = if ($failureParts.Count -gt 0) {
            "Renderer verification rejected the themed target ($($failureParts -join ', '))."
          } else {
            'Renderer verification rejected the themed target.'
          }
        } else {
          $failureDetail = 'The renderer returned a failing result.'
        }
      }
      if (-not $failureDetail) { $failureDetail = "Native command exited with code $nativeExitCode." }
      throw "Command failed: $Program. $failureDetail"
    }
    if (-not $parsed) { throw "Command returned no JSON: $Program" }
    $parsed
  } finally {
    [Console]::OutputEncoding = $previousConsoleEncoding
    $OutputEncoding = $previousOutputEncoding
  }
}

function Get-ThemeStatus([string]$Node) {
  Invoke-NodeJson $Node $ThemeTool @('status', '--state', $StatePath)
}

function Get-CodexCompatibility([string]$Node, [object]$Install) {
  Invoke-NodeJson $Node $ThemeTool @(
    'compatibility', '--platform', 'windows', '--codex-version', [string]$Install.Package.Version
  )
}

function Assert-CompatibleCodexBuild([string]$Node, [object]$Install) {
  $compatibility = Get-CodexCompatibility $Node $Install
  if ([string]$compatibility.status -notin @('PASS', 'PARTIAL')) {
    throw "Codex build $($Install.Package.Version) is UNSUPPORTED. Ask this Skill to run its read-only compatibility audit; no restart or injection was attempted."
  }
  $compatibility
}

function Invoke-NativeCompatibilityProbe([string]$Node, [object]$Install) {
  if (-not (Test-VerifiedCdp $Port $Install)) {
    throw 'No verified Codex loopback endpoint is available for the read-only compatibility audit.'
  }
  Invoke-NodeJson $Node $Injector @(
    'compatibility-probe', '--platform', 'windows',
    '--codex-version', [string]$Install.Package.Version,
    '--port', "$Port", '--timeout-ms', '5000'
  )
}

function Resolve-NextTheme([string]$Node) {
  Invoke-NodeJson $Node $ThemeTool @('resolve-next', '--catalog', $Catalog, '--state', $StatePath)
}

function Resolve-LoadedTheme([string]$Node) {
  $status = Get-ThemeStatus $Node
  $id = $status.loadedTheme
  if (-not $id) { throw 'No verified loadedTheme is recorded.' }
  Invoke-NodeJson $Node $ThemeTool @('resolve', '--catalog', $Catalog, '--theme', [string]$id)
}

function Get-CodexProcesses([object]$Install) {
  @(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -and ([IO.Path]::GetFullPath($_.Path) -eq $Install.Exe) } catch { $false }
  })
}

function Get-CodexProcessSnapshots([object]$Install) {
  $snapshots = [Collections.Generic.List[object]]::new()
  foreach ($process in @(Get-CodexProcesses $Install)) {
    try {
      if (-not (Test-SamePath $process.Path $Install.Exe)) {
        throw 'executable path no longer matches the discovered Codex installation'
      }
      $snapshots.Add([pscustomobject]@{
        Id = $process.Id
        Path = [IO.Path]::GetFullPath($process.Path)
        StartedAt = $process.StartTime.ToUniversalTime().ToString('o')
      })
    } catch {
      throw "Could not capture Codex PID $($process.Id) for a controlled restart: $($_.Exception.Message)"
    }
  }
  @($snapshots)
}

function Test-CapturedCodexProcess([object]$Captured, [object]$Process, [object]$Install) {
  if (-not $Captured -or -not $Process -or -not $Captured.Id -or -not $Captured.StartedAt) { return $false }
  try {
    $pathMatches = (Test-SamePath $Process.Path $Install.Exe) -and (Test-SamePath $Process.Path $Captured.Path)
    $startMatches = $Process.StartTime.ToUniversalTime().ToString('o') -eq [string]$Captured.StartedAt
    return $pathMatches -and $startMatches
  } catch { return $false }
}

function Get-CapturedCodexProcess([object]$Captured, [object]$Install) {
  $process = Get-Process -Id ([int]$Captured.Id) -ErrorAction SilentlyContinue
  if (-not $process) { return $null }
  if (-not (Test-CapturedCodexProcess $Captured $process $Install)) {
    throw "PID $($Captured.Id) no longer matches the captured Codex process; refusing to close or terminate it."
  }
  $process
}

function Request-CodexGracefulClose([object[]]$Captured, [object]$Install) {
  foreach ($entry in @($Captured)) {
    $process = Get-CapturedCodexProcess $entry $Install
    if ($process) {
      try { [void]$process.CloseMainWindow() }
      catch { throw "Could not request a graceful close for captured Codex PID $($entry.Id): $($_.Exception.Message)" }
    }
  }
}

function Wait-CapturedCodexExit([object[]]$Captured, [object]$Install, [int]$TimeoutMilliseconds) {
  $deadline = (Get-Date).AddMilliseconds($TimeoutMilliseconds)
  do {
    $remaining = [Collections.Generic.List[object]]::new()
    foreach ($entry in @($Captured)) {
      $process = Get-CapturedCodexProcess $entry $Install
      if ($process) { $remaining.Add($process) }
    }
    if ($remaining.Count -eq 0) { return @() }
    if ((Get-Date) -ge $deadline) { return @($remaining) }
    Start-Sleep -Milliseconds 200
  } while ($true)
}

function Stop-CapturedCodexProcesses([object[]]$Captured, [object]$Install) {
  foreach ($entry in @($Captured)) {
    $process = Get-CapturedCodexProcess $entry $Install
    if ($process) {
      Stop-Process -Id $process.Id -ErrorAction Stop
    }
  }
}

function Wait-ThemePortReleased([int]$TimeoutMilliseconds) {
  $deadline = (Get-Date).AddMilliseconds($TimeoutMilliseconds)
  while (@(Get-TcpListenerSnapshot $Port).Count -gt 0) {
    if ((Get-Date) -ge $deadline) {
      throw "Port $Port remained active after the controlled Codex restart; refusing to reuse it."
    }
    Start-Sleep -Milliseconds 200
  }
}

function Restart-CodexForThemedLaunch([object]$Install) {
  $captured = @(Get-CodexProcessSnapshots $Install)
  if ($captured.Count -gt 0) {
    Request-CodexGracefulClose $captured $Install
    $remaining = @(Wait-CapturedCodexExit $captured $Install 5000)
    if ($remaining.Count -gt 0) {
      Stop-CapturedCodexProcesses $captured $Install
      $remaining = @(Wait-CapturedCodexExit $captured $Install 10000)
    }
    if ($remaining.Count -gt 0) {
      $processIds = ($remaining.Id | Sort-Object) -join ', '
      throw "Captured Codex PIDs did not exit after the controlled restart: $processIds"
    }
  }
  Wait-ThemePortReleased 5000
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
        description = $shortcut.Description
        targetExists = [bool](Test-Path -LiteralPath $shortcut.TargetPath -PathType Leaf)
        iconExists = [bool]($iconPath -and (Test-Path -LiteralPath $iconPath -PathType Leaf))
      }
    }
  }
}

function Get-LegacyDreamSkinSignature {
  $repositoryRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $SkillRoot))
  $workingDirectory = Join-Path $repositoryRoot '.worktrees\one-piece-paper-theme\windows'
  $scriptPath = Join-Path $workingDirectory 'scripts\start-dream-skin.ps1'
  [pscustomobject]@{
    Target = (Get-Command powershell.exe -ErrorAction Stop).Source
    Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Port 9335"
    WorkingDirectory = $workingDirectory
    IconLocation = "$(Join-Path $env:LOCALAPPDATA 'CodexDreamSkin\codex.ico'),0"
    Description = $LegacyDreamSkinDescription
  }
}

function Test-LegacyDreamSkinLauncherOwned([object]$Snapshot) {
  if (-not $Snapshot.Exists) { return $false }
  $signature = Get-LegacyDreamSkinSignature
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  -not $Snapshot.IsReparsePoint -and
    $Snapshot.OwnerSid -eq $currentSid -and
    (Test-SamePath $Snapshot.Target $signature.Target) -and
    $Snapshot.Arguments -eq $signature.Arguments -and
    (Test-SamePath $Snapshot.WorkingDirectory $signature.WorkingDirectory) -and
    $Snapshot.IconLocation -eq $signature.IconLocation -and
    $Snapshot.Description -eq $signature.Description
}

function Get-LegacyDreamSkinLauncherStatus {
  $desktop = [Environment]::GetFolderPath('Desktop')
  if (-not $desktop) { return $null }
  $path = Join-Path $desktop $LegacyDreamSkinLauncherName
  $shell = New-Object -ComObject WScript.Shell
  $snapshot = Get-ShortcutSnapshot $path $shell
  if (-not $snapshot.Exists) { return $null }
  [pscustomobject]@{
    path = $snapshot.Path
    owner = $snapshot.Owner
    target = $snapshot.Target
    arguments = $snapshot.Arguments
    workingDirectory = $snapshot.WorkingDirectory
    iconLocation = $snapshot.IconLocation
    description = $snapshot.Description
    ownership = if (Test-LegacyDreamSkinLauncherOwned $snapshot) { 'known-legacy' } else { 'unknown' }
  }
}

function Get-TcpListenerSnapshot([int]$CandidatePort) {
  $endpoints = @([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
    Where-Object { $_.Port -eq $CandidatePort })
  if ($endpoints.Count -eq 0) { return @() }

  $netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
  if (-not (Test-Path -LiteralPath $netstat -PathType Leaf)) { throw "Windows netstat was not found: $netstat" }
  $lines = & $netstat -ano -p tcp
  $netstatExit = $LASTEXITCODE
  if ($netstatExit -ne 0) { throw "netstat failed while resolving listener ownership for port $CandidatePort." }

  $listeners = [Collections.Generic.List[object]]::new()
  foreach ($endpoint in $endpoints) {
    $endpointAddress = $endpoint.Address.ToString()
    $owners = [Collections.Generic.List[int]]::new()
    foreach ($line in $lines) {
      $parts = @(([string]$line).Trim() -split '\s+')
      if ($parts.Count -lt 5 -or $parts[0] -ne 'TCP' -or $parts[2] -notmatch ':0$') { continue }
      $local = [string]$parts[1]
      if ($local -match '^\[(?<address>.+)\]:(?<port>\d+)$') {
        $rowAddress = $Matches['address']
        $rowPort = [int]$Matches['port']
      } elseif ($local -match '^(?<address>[^:]+):(?<port>\d+)$') {
        $rowAddress = $Matches['address']
        $rowPort = [int]$Matches['port']
      } else { continue }
      if ($rowPort -ne $CandidatePort -or $rowAddress -ne $endpointAddress) { continue }
      $ownerId = 0
      if ([int]::TryParse([string]$parts[-1], [ref]$ownerId)) { $owners.Add($ownerId) }
    }
    if ($owners.Count -ne 1) {
      throw "Could not resolve exactly one owner for TCP listener $endpointAddress`:$CandidatePort."
    }
    $listeners.Add([pscustomobject]@{ LocalAddress = $endpointAddress; OwningProcess = $owners[0] })
  }
  @($listeners)
}

function Test-VerifiedCdp([int]$CandidatePort, [object]$Install) {
  try {
    $listeners = @(Get-TcpListenerSnapshot $CandidatePort)
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
  try {
    $recordedStart = [datetimeoffset]$runtime.injectorStartedAt
    $actualStart = [datetimeoffset]$process.StartTime.ToUniversalTime()
    $sameStart = [math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -le 1
  } catch { $sameStart = $false }
  $sameCommand = $command.CommandLine -and
    $command.CommandLine.IndexOf([string]$runtime.injectorPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $command.CommandLine -match '(?:^|\s)watch(?:\s|$)'
  if (-not ($sameNode -and $sameStart -and $sameCommand)) {
    throw "PID $($runtime.injectorPid) no longer matches the recorded theme watcher; refusing to stop it."
  }
  Stop-Process -Id $process.Id -ErrorAction Stop
  [void]$process.WaitForExit(3000)
}

function Assert-ThemeLaunchPreconditions([object]$Install) {
  $running = @(Get-CodexProcesses $Install)
  if ($running.Count -gt 0) {
    $processIds = ($running.Id | Sort-Object) -join ', '
    throw "Codex is still running (PIDs: $processIds). Close Codex completely, then open Codex Themes again. Theme changes are next-launch only; no new Codex instance was started."
  }
  if (@(Get-TcpListenerSnapshot $Port).Count -gt 0) {
    throw "Port $Port is already in use; refusing to attach to an unknown listener. No new Codex instance was started."
  }
}

function Show-ThemeLauncherFailure([string]$Message) {
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    [void][Windows.Forms.MessageBox]::Show(
      $Message,
      'Codex Themes',
      [Windows.Forms.MessageBoxButtons]::OK,
      [Windows.Forms.MessageBoxIcon]::Warning
    )
  } catch {}
}

function Write-Runtime(
  [Diagnostics.Process]$Process,
  [string]$Node,
  [object]$ResolvedTheme,
  [string]$PayloadHash
) {
  New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
  $record = [ordered]@{
    schemaVersion = 2
    port = $Port
    injectorPid = $Process.Id
    injectorStartedAt = $Process.StartTime.ToUniversalTime().ToString('o')
    injectorPath = (Resolve-Path -LiteralPath $Injector).Path
    nodePath = (Resolve-Path -LiteralPath $Node).Path
    themeId = $ResolvedTheme.id
    themeDir = $ResolvedTheme.directory
    themeHash = $ResolvedTheme.hash
    payloadHash = $PayloadHash
  }
  $temporary = "$RuntimePath.$PID.tmp"
  $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
  Move-Item -LiteralPath $temporary -Destination $RuntimePath -Force
}

function Test-SamePath([string]$First, [string]$Second) {
  if (-not $First -or -not $Second) { return $false }
  try { return [IO.Path]::GetFullPath($First).TrimEnd('\') -eq [IO.Path]::GetFullPath($Second).TrimEnd('\') }
  catch { return $false }
}

function Get-ShortcutSnapshot([string]$Path, [object]$Shell) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return [pscustomobject]@{ Exists = $false; Path = $Path }
  }
  $item = Get-Item -LiteralPath $Path -Force
  $shortcut = $Shell.CreateShortcut($Path)
  $owner = (Get-Acl -LiteralPath $Path).Owner
  try {
    $ownerSid = ([Security.Principal.NTAccount]$owner).Translate([Security.Principal.SecurityIdentifier]).Value
  } catch { $ownerSid = $null }
  [pscustomobject]@{
    Exists = $true
    Path = $Path
    IsReparsePoint = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    Owner = $owner
    OwnerSid = $ownerSid
    Target = [string]$shortcut.TargetPath
    Arguments = [string]$shortcut.Arguments
    WorkingDirectory = [string]$shortcut.WorkingDirectory
    IconLocation = [string]$shortcut.IconLocation
    Description = [string]$shortcut.Description
    Hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
  }
}

function Get-ThemesLauncherClassification(
  [object]$Snapshot,
  [string]$PowerShell,
  [string]$StableIcon
) {
  if (-not $Snapshot.Exists) { return [pscustomobject]@{ Kind = 'missing'; Port = $Port } }
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $baseCommon = -not $Snapshot.IsReparsePoint -and
    $Snapshot.OwnerSid -eq $currentSid -and
    (Test-SamePath $Snapshot.Target $PowerShell) -and
    $Snapshot.IconLocation -eq "$StableIcon,0"
  if (-not $baseCommon) { return [pscustomobject]@{ Kind = 'unknown'; Port = $null } }

  $scriptPattern = [regex]::Escape([IO.Path]::GetFullPath($PSCommandPath))
  $legacyPattern = "^-NoProfile -File `"$scriptPattern`" -Action start -Port (?<port>\d+) -AuthorizedRestart$"
  $currentPattern = "^-NoProfile -STA -File `"$scriptPattern`" -Action start -ShowThemeSelector -Port (?<port>\d+) -AuthorizedRestart$"
  $sameWorkingDirectory = Test-SamePath $Snapshot.WorkingDirectory $ScriptRoot
  $match = [regex]::Match($Snapshot.Arguments, $currentPattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if ($sameWorkingDirectory -and $match.Success -and $Snapshot.Description -eq $ThemesLauncherDescription) {
    $launcherPort = [int]$match.Groups['port'].Value
    if ($launcherPort -ge 1024 -and $launcherPort -le 65535) {
      return [pscustomobject]@{ Kind = 'current'; Port = $launcherPort }
    }
  }
  $match = [regex]::Match($Snapshot.Arguments, $legacyPattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if ($sameWorkingDirectory -and $match.Success -and [string]::IsNullOrEmpty($Snapshot.Description)) {
    $launcherPort = [int]$match.Groups['port'].Value
    if ($launcherPort -ge 1024 -and $launcherPort -le 65535) {
      return [pscustomobject]@{ Kind = 'legacy'; Port = $launcherPort }
    }
  }

  $manifest = Get-SkillManifest
  $approved = $manifest.approvedRelocation
  if ($approved -and $Snapshot.Description -eq $ThemesLauncherDescription) {
    $approvedScript = Join-Path ([string]$approved.sourceRoot) 'scripts\windows-theme.ps1'
    $approvedWorkingDirectory = Split-Path -Parent $approvedScript
    $relocationPattern = '^-NoProfile -STA -File "(?<script>[^"]+)" -Action start -ShowThemeSelector -Port (?<port>\d+) -AuthorizedRestart$'
    $relocation = [regex]::Match(
      $Snapshot.Arguments,
      $relocationPattern,
      [Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if ($relocation.Success -and
        (Test-SamePath $relocation.Groups['script'].Value $approvedScript) -and
        (Test-SamePath $Snapshot.WorkingDirectory $approvedWorkingDirectory) -and
        (Test-Path -LiteralPath $approvedScript -PathType Leaf)) {
      $approvedHash = (Get-FileHash -LiteralPath $approvedScript -Algorithm SHA256).Hash.ToLowerInvariant()
      $expectedHash = ([string]$approved.sourceLauncherScriptSha256).ToLowerInvariant()
      $launcherPort = [int]$relocation.Groups['port'].Value
      if ($approvedHash -eq $expectedHash -and $launcherPort -ge 1024 -and $launcherPort -le 65535) {
        return [pscustomobject]@{ Kind = 'relocatable'; Port = $launcherPort }
      }
    }
  }
  [pscustomobject]@{ Kind = 'unknown'; Port = $null }
}

function Test-OriginalLauncherOwned(
  [object]$Snapshot,
  [object]$Install,
  [string]$Desktop,
  [string]$StableIcon
) {
  if (-not $Snapshot.Exists) { return $true }
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $descriptionOwned = [string]::IsNullOrEmpty($Snapshot.Description) -or
    $Snapshot.Description -eq $OriginalLauncherDescription
  -not $Snapshot.IsReparsePoint -and
    $Snapshot.OwnerSid -eq $currentSid -and
    (Test-SamePath $Snapshot.Target (Get-Command explorer.exe -ErrorAction Stop).Source) -and
    $Snapshot.Arguments -eq "shell:AppsFolder\$($Install.AppId)" -and
    (Test-SamePath $Snapshot.WorkingDirectory $Desktop) -and
    $Snapshot.IconLocation -eq "$StableIcon,0" -and
    $descriptionOwned
}

function Write-ShortcutFile(
  [object]$Shell,
  [string]$Path,
  [string]$Target,
  [string]$Arguments,
  [string]$WorkingDirectory,
  [string]$IconLocation,
  [string]$Description
) {
  $shortcut = $Shell.CreateShortcut($Path)
  $shortcut.TargetPath = $Target
  $shortcut.Arguments = $Arguments
  $shortcut.WorkingDirectory = $WorkingDirectory
  $shortcut.IconLocation = $IconLocation
  $shortcut.Description = $Description
  $shortcut.Save()
}

function Undo-ThemesLauncherTransaction([object]$Transaction) {
  $path = [string]$Transaction.Path
  $backup = [string]$Transaction.BackupPath
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    if (-not $Transaction.PreviousExists) { return }
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
      throw "Codex Themes rollback has no installed file or verified backup: $backup"
    }
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $Transaction.PreviousHash) {
      throw "Codex Themes rollback backup changed; preserving it for manual recovery: $backup"
    }
    Move-Item -LiteralPath $backup -Destination $path
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Transaction.PreviousHash) {
      throw "Codex Themes rollback could not verify the restored shortcut: $path"
    }
    return
  }

  $currentHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
  if ($currentHash -ne $Transaction.InstalledHash) {
    throw "Codex Themes changed after installation; refusing rollback. Preserved backup: $backup"
  }
  if (-not $Transaction.PreviousExists) {
    Remove-Item -LiteralPath $path -Force
    return
  }
  if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
    throw "Codex Themes rollback backup is missing: $backup"
  }
  if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $Transaction.PreviousHash) {
    throw "Codex Themes rollback backup changed; preserving it for manual recovery: $backup"
  }

  $failed = "$path.$PID.failed"
  [IO.File]::Replace($backup, $path, $failed, $true)
  if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Transaction.PreviousHash) {
    throw "Codex Themes rollback could not verify the restored shortcut. Recovery artifact: $failed"
  }
  if (Test-Path -LiteralPath $failed -PathType Leaf) {
    $failedHash = (Get-FileHash -LiteralPath $failed -Algorithm SHA256).Hash
    if ($failedHash -eq $Transaction.InstalledHash) {
      Remove-Item -LiteralPath $failed -Force -ErrorAction SilentlyContinue
    }
  }
}

function Complete-ThemesLauncherTransaction([object]$Transaction) {
  $path = [string]$Transaction.Path
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Codex Themes disappeared before transaction commit: $path"
  }
  if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Transaction.InstalledHash) {
    throw "Codex Themes changed before transaction commit; refusing to delete its backup."
  }
  if ($Transaction.PreviousExists) {
    $backup = [string]$Transaction.BackupPath
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
      throw "Codex Themes backup disappeared before transaction commit: $backup"
    }
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $Transaction.PreviousHash) {
      throw "Codex Themes backup changed before transaction commit: $backup"
    }
    Remove-Item -LiteralPath $backup -Force
  }
}

function Install-ThemesLauncher(
  [object]$Shell,
  [string]$Path,
  [string]$PowerShell,
  [string]$StableIcon,
  [int]$LauncherPort,
  [object]$Existing
) {
  $temporary = "$Path.$PID.tmp.lnk"
  $backup = "$Path.$PID.bak"
  $arguments = "-NoProfile -STA -File `"$PSCommandPath`" -Action start -ShowThemeSelector -Port $LauncherPort -AuthorizedRestart"
  $transaction = $null
  try {
    Write-ShortcutFile $Shell $temporary $PowerShell $arguments $ScriptRoot "$StableIcon,0" $ThemesLauncherDescription
    $candidate = Get-ShortcutSnapshot $temporary $Shell
    $candidateClass = Get-ThemesLauncherClassification $candidate $PowerShell $StableIcon
    if ($candidateClass.Kind -ne 'current' -or $candidateClass.Port -ne $LauncherPort) {
      throw 'The staged Codex Themes shortcut did not match the managed selector signature.'
    }
    $installedHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash

    if ($Existing.Exists) {
      $currentHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
      if ($currentHash -ne $Existing.Hash) { throw 'Codex Themes changed during migration; refusing to replace it.' }
      [IO.File]::Replace($temporary, $Path, $backup, $true)
    } else {
      Move-Item -LiteralPath $temporary -Destination $Path
    }
    $transaction = [pscustomobject]@{
      Path = $Path
      PreviousExists = [bool]$Existing.Exists
      PreviousHash = if ($Existing.Exists) { [string]$Existing.Hash } else { $null }
      BackupPath = $backup
      InstalledHash = $installedHash
    }

    $installed = Get-ShortcutSnapshot $Path $Shell
    $installedClass = Get-ThemesLauncherClassification $installed $PowerShell $StableIcon
    if ($installedClass.Kind -ne 'current' -or $installedClass.Port -ne $LauncherPort) {
      throw 'Codex Themes failed post-save verification.'
    }
    return $transaction
  } catch {
    $failure = $_.Exception.Message
    if ($transaction) {
      try { Undo-ThemesLauncherTransaction $transaction }
      catch {
        throw "$failure Rollback was incomplete: $($_.Exception.Message)"
      }
    }
    throw $failure
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
}

function Ensure-DesktopLaunchers([object]$Install) {
  $desktop = [Environment]::GetFolderPath('Desktop')
  if (-not $desktop) { throw 'Could not resolve the Desktop directory.' }
  $shell = New-Object -ComObject WScript.Shell
  $powerShell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $powerShell) { $powerShell = (Get-Process -Id $PID).Path }

  $themesPath = Join-Path $desktop 'Codex Themes.lnk'
  $originalPath = Join-Path $desktop 'Codex Original.lnk'
  $iconSource = Join-Path $Install.Root 'app\resources\icon-chatgpt.ico'
  if (-not (Test-Path -LiteralPath $iconSource -PathType Leaf)) {
    throw "Official Codex icon not found: $iconSource"
  }
  New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
  $stableIcon = Join-Path $StateRoot 'codex.ico'
  if (-not (Test-Path -LiteralPath $stableIcon -PathType Leaf)) {
    Copy-Item -LiteralPath $iconSource -Destination $stableIcon
  }

  $themesBefore = Get-ShortcutSnapshot $themesPath $shell
  $originalBefore = Get-ShortcutSnapshot $originalPath $shell
  $themesClass = Get-ThemesLauncherClassification $themesBefore $powerShell $stableIcon
  if ($themesClass.Kind -eq 'unknown') {
    throw 'Codex Themes exists but is not a verified theme-owned launcher; refusing to overwrite it.'
  }
  if (-not (Test-OriginalLauncherOwned $originalBefore $Install $desktop $stableIcon)) {
    throw 'Codex Original exists but is not a verified theme-owned launcher; refusing to overwrite it.'
  }

  $launcherPort = if ($themesClass.Kind -in @('legacy', 'current', 'relocatable')) {
    [int]$themesClass.Port
  } else {
    $Port
  }
  $themesTransaction = $null
  $temporaryOriginal = "$originalPath.$PID.tmp.lnk"
  $createdOriginal = $false
  $createdOriginalHash = $null
  try {
    if ($themesClass.Kind -ne 'current') {
      $themesTransaction = Install-ThemesLauncher $shell $themesPath $powerShell $stableIcon $launcherPort $themesBefore
    }

    if (-not $originalBefore.Exists) {
      Write-ShortcutFile $shell $temporaryOriginal (Get-Command explorer.exe -ErrorAction Stop).Source `
        "shell:AppsFolder\$($Install.AppId)" $desktop "$stableIcon,0" $OriginalLauncherDescription
      $stagedOriginal = Get-ShortcutSnapshot $temporaryOriginal $shell
      if (-not (Test-OriginalLauncherOwned $stagedOriginal $Install $desktop $stableIcon)) {
        throw 'The staged Codex Original shortcut did not match the managed native signature.'
      }
      $createdOriginalHash = (Get-FileHash -LiteralPath $temporaryOriginal -Algorithm SHA256).Hash
      Move-Item -LiteralPath $temporaryOriginal -Destination $originalPath
      $createdOriginal = $true
      $installedOriginal = Get-ShortcutSnapshot $originalPath $shell
      if (-not (Test-OriginalLauncherOwned $installedOriginal $Install $desktop $stableIcon)) {
        throw 'Codex Original failed post-save verification.'
      }
    } elseif ($originalBefore.Hash -ne (Get-FileHash -LiteralPath $originalPath -Algorithm SHA256).Hash) {
      throw 'Codex Original changed while updating Codex Themes; no Original changes were permitted.'
    }

    if ($themesTransaction) { Complete-ThemesLauncherTransaction $themesTransaction }
  } catch {
    $failure = $_.Exception.Message
    $rollbackFailures = [Collections.Generic.List[string]]::new()
    if ($createdOriginal -and (Test-Path -LiteralPath $originalPath -PathType Leaf)) {
      try {
        $currentOriginalHash = (Get-FileHash -LiteralPath $originalPath -Algorithm SHA256).Hash
        if ($currentOriginalHash -ne $createdOriginalHash) {
          throw "Created Codex Original changed; refusing to delete it: $originalPath"
        }
        Remove-Item -LiteralPath $originalPath -Force
      } catch { $rollbackFailures.Add($_.Exception.Message) }
    }
    if ($themesTransaction) {
      try { Undo-ThemesLauncherTransaction $themesTransaction }
      catch { $rollbackFailures.Add($_.Exception.Message) }
    }
    if ($rollbackFailures.Count -gt 0) {
      throw "$failure Rollback was incomplete: $($rollbackFailures -join ' | ')"
    }
    throw $failure
  } finally {
    Remove-Item -LiteralPath $temporaryOriginal -Force -ErrorAction SilentlyContinue
  }

  Write-Host 'Codex Themes now opens the visual selector. Codex Original was preserved as the native escape hatch.'
}

function Archive-LegacyDreamSkinLauncher {
  $desktop = [Environment]::GetFolderPath('Desktop')
  if (-not $desktop) { throw 'Could not resolve the Desktop directory.' }
  $path = Join-Path $desktop $LegacyDreamSkinLauncherName
  $shell = New-Object -ComObject WScript.Shell
  $snapshot = Get-ShortcutSnapshot $path $shell
  if (-not $snapshot.Exists) { return $null }
  if (-not (Test-LegacyDreamSkinLauncherOwned $snapshot)) {
    throw 'Codex Dream Skin exists but is not the verified legacy One Piece launcher; refusing to replace or remove it.'
  }

  $archiveRoot = Join-Path $StateRoot 'legacy-launchers'
  New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
  $archivePath = Join-Path $archiveRoot "Codex Dream Skin.legacy.$($snapshot.Hash.ToLowerInvariant()).lnk"
  if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
    throw "A legacy Dream Skin backup already exists: $archivePath. Refusing to remove the desktop launcher again."
  }
  $currentHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
  if ($currentHash -ne $snapshot.Hash) {
    throw 'Codex Dream Skin changed during migration; refusing to move it.'
  }

  Move-Item -LiteralPath $path -Destination $archivePath
  if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $snapshot.Hash) {
    try {
      if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Move-Item -LiteralPath $archivePath -Destination $path
      }
    } catch {}
    throw "Codex Dream Skin archive verification failed; the launcher was restored when possible. Backup: $archivePath"
  }
  return $archivePath
}

function Get-ThemeStateMutexName {
  $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value.Replace('-', '_')
  "Global\CodexThemeStudio.Start.$userSid"
}

function Invoke-WithThemeStateLock([scriptblock]$Operation) {
  $mutex = [Threading.Mutex]::new($false, (Get-ThemeStateMutexName))
  $lockTaken = $false
  try {
    try { $lockTaken = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $lockTaken = $true }
    if (-not $lockTaken) { throw 'Another Codex Themes selection, state change, or start is already in progress.' }
    & $Operation
  } finally {
    if ($lockTaken) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
  }
}

function Start-ThemeWatcher(
  [object]$NodeInfo,
  [object]$ResolvedTheme,
  [string]$PayloadHash
) {
  New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
  $quotedInjector = '"' + $Injector + '"'
  $quotedTheme = '"' + [string]$ResolvedTheme.directory + '"'
  $quotedWatchStatus = '"' + $WatchStatusPath + '"'
  $watcher = Start-Process -FilePath $NodeInfo.Path -ArgumentList @(
    $quotedInjector, 'watch', '--theme-dir', $quotedTheme, '--port', "$Port",
    '--status-file', $quotedWatchStatus
  ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath
  Start-Sleep -Milliseconds 350
  if ($watcher.HasExited) { throw "Theme watcher exited during startup. See $StderrPath" }
  Write-Runtime $watcher $NodeInfo.Path $ResolvedTheme $PayloadHash
  $watcher
}

function Test-ApprovedRuntimeRelocation(
  [object]$Runtime,
  [object]$ResolvedTheme,
  [object]$NodeInfo
) {
  $manifest = Get-SkillManifest
  $approved = $manifest.approvedRelocation
  if (-not $approved) { return $false }
  $sourceRoot = [string]$approved.sourceRoot
  $sourceInjector = Join-Path $sourceRoot 'scripts\injector.mjs'
  $sourceThemeTool = Join-Path $sourceRoot 'scripts\theme-tool.mjs'
  $sourceCatalog = Join-Path $sourceRoot 'assets\presets'
  $sourceThemeDirectory = Join-Path $sourceCatalog ([string]$ResolvedTheme.id)
  if (-not (Test-SamePath ([string]$Runtime.injectorPath) $sourceInjector) -or
      -not (Test-SamePath ([string]$Runtime.themeDir) $sourceThemeDirectory) -or
      -not (Test-Path -LiteralPath $sourceInjector -PathType Leaf) -or
      -not (Test-Path -LiteralPath $sourceThemeTool -PathType Leaf)) {
    return $false
  }
  $injectorHash = (Get-FileHash -LiteralPath $sourceInjector -Algorithm SHA256).Hash.ToLowerInvariant()
  $toolHash = (Get-FileHash -LiteralPath $sourceThemeTool -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($injectorHash -ne ([string]$approved.sourceInjectorSha256).ToLowerInvariant() -or
      $toolHash -ne ([string]$approved.sourceThemeToolSha256).ToLowerInvariant()) {
    return $false
  }
  try {
    $sourceResolved = Invoke-NodeJson $NodeInfo.Path $sourceThemeTool @(
      'resolve', '--catalog', $sourceCatalog, '--theme', [string]$ResolvedTheme.id
    )
  } catch {
    return $false
  }
  [bool]($sourceResolved.pass -and
    (Test-SamePath ([string]$sourceResolved.directory) ([string]$Runtime.themeDir)) -and
    [string]$sourceResolved.hash -eq [string]$Runtime.themeHash)
}

function Refresh-LoadedTheme([object]$Install, [object]$NodeInfo) {
  if (-not (Test-VerifiedCdp $Port $Install)) {
    throw 'No verified Codex loopback endpoint is available for a hot theme refresh.'
  }
  if (-not (Test-Path -LiteralPath $RuntimePath -PathType Leaf)) {
    throw 'No recorded theme watcher is available; refusing to start an unpaired watcher.'
  }
  try { $runtime = Get-Content -LiteralPath $RuntimePath -Raw | ConvertFrom-Json }
  catch { throw "Could not parse recorded injector state; refusing hot refresh: $($_.Exception.Message)" }

  $themeStatus = Get-ThemeStatus $NodeInfo.Path
  $recoveringRolledBackRefresh = -not $themeStatus.loadedTheme -and [bool]$themeStatus.nextLaunchTheme
  $resolved = if ($themeStatus.loadedTheme) {
    Resolve-LoadedTheme $NodeInfo.Path
  } elseif ($recoveringRolledBackRefresh) {
    Resolve-NextTheme $NodeInfo.Path
  } else {
    throw 'No loaded or staged theme is available for a hot refresh.'
  }
  $sameThemeDirectory = Test-SamePath ([string]$runtime.themeDir) ([string]$resolved.directory)
  $approvedRelocation = -not $sameThemeDirectory -and
    (Test-ApprovedRuntimeRelocation $runtime $resolved $NodeInfo)
  if ([int]$runtime.port -ne $Port -or
      [string]$runtime.themeId -ne [string]$resolved.id -or
      -not ($sameThemeDirectory -or $approvedRelocation)) {
    throw 'Recorded watcher identity does not match the loaded theme; refusing hot refresh.'
  }
  $recordedWatcher = Get-Process -Id ([int]$runtime.injectorPid) -ErrorAction SilentlyContinue

  $checked = Invoke-NodeJson $NodeInfo.Path $Injector @(
    'check', '--theme-dir', [string]$resolved.directory
  )
  $payloadHash = [string]$checked.payloadHash
  if (-not $checked.pass -or $payloadHash -notmatch '^[0-9a-f]{64}$') {
    throw 'Theme payload validation returned an invalid payload hash.'
  }

  $recoveringStoppedWatcher = $false
  if (-not $recordedWatcher -and -not $recoveringRolledBackRefresh) {
    try {
      $liveVerification = Invoke-NodeJson $NodeInfo.Path $Injector @(
        'verify', '--theme-dir', [string]$resolved.directory, '--port', "$Port",
        '--timeout-ms', "$RefreshRendererTimeoutMilliseconds"
      )
    } catch {
      throw "The recorded theme watcher is no longer running and the live renderer could not prove the exact theme payload: $($_.Exception.Message)"
    }
    if (-not $liveVerification.pass -or [string]$liveVerification.payloadHash -ne $payloadHash) {
      throw 'The recorded theme watcher is no longer running and the live renderer payload does not match; refusing to guess a replacement.'
    }
    $recoveringStoppedWatcher = $true
  }

  $watcher = $null
  if ($recordedWatcher) { Stop-RecordedInjector }
  try {
    $watcher = Start-ThemeWatcher $NodeInfo $resolved $payloadHash
    $verified = Invoke-NodeJson $NodeInfo.Path $Injector @(
      'verify', '--theme-dir', [string]$resolved.directory, '--port', "$Port"
    )
    if (-not $verified.pass -or [string]$verified.payloadHash -ne $payloadHash) {
      throw 'Refreshed theme payload did not pass renderer verification.'
    }
    & $NodeInfo.Path $ThemeTool mark-loaded --state $StatePath --theme $resolved.id --hash $payloadHash
    if ($LASTEXITCODE -ne 0) { throw 'Could not record the refreshed loaded theme.' }
    [pscustomobject]@{
      pass = $true
      status = 'PARTIAL'
      action = 'refresh'
      restartedCodex = $false
      themeId = $resolved.id
      themeHash = $resolved.hash
      payloadHash = $payloadHash
      cssHash = $verified.cssHash
      watcherPid = $watcher.Id
      recoveredStoppedWatcher = $recoveringStoppedWatcher
      relocatedFrom = if ($approvedRelocation) { [string]$runtime.themeDir } else { $null }
    }
  } catch {
    try { Stop-RecordedInjector } catch {}
    try { & $NodeInfo.Path $Injector remove --port $Port *> $null } catch {}
    try {
      & $NodeInfo.Path $ThemeTool mark-restored --state $StatePath *> $null
      & $NodeInfo.Path $ThemeTool select --catalog $Catalog --state $StatePath --theme $resolved.id *> $null
    } catch {}
    throw "Theme hot refresh failed and the partial injection was removed: $($_.Exception.Message)"
  }
}

function Start-NextTheme([object]$Install, [object]$NodeInfo, [switch]$RestartExisting) {
  [void](Assert-CompatibleCodexBuild $NodeInfo.Path $Install)
  $resolved = Resolve-NextTheme $NodeInfo.Path
  $checked = Invoke-NodeJson $NodeInfo.Path $Injector @('check', '--theme-dir', [string]$resolved.directory)
  $payloadHash = [string]$checked.payloadHash
  if (-not $checked.pass -or $payloadHash -notmatch '^[0-9a-f]{64}$') {
    throw 'Theme payload validation returned an invalid payload hash.'
  }

  if ($RestartExisting) { Restart-CodexForThemedLaunch $Install }
  Assert-ThemeLaunchPreconditions $Install
  Start-Process -FilePath $Install.Exe -ArgumentList @(
    '--remote-debugging-address=127.0.0.1', "--remote-debugging-port=$Port"
  ) | Out-Null
  $deadline = (Get-Date).AddSeconds($ColdStartEndpointTimeoutSeconds)
  while (-not (Test-VerifiedCdp $Port $Install)) {
    if ((Get-Date) -ge $deadline) { throw "Codex did not expose a verified loopback endpoint on port $Port." }
    Start-Sleep -Milliseconds 400
  }

  Stop-RecordedInjector
  try {
    $injected = Invoke-NodeJson $NodeInfo.Path $Injector @(
      'once', '--theme-dir', [string]$resolved.directory, '--port', "$Port",
      '--timeout-ms', "$ColdStartRendererTimeoutMilliseconds"
    )
  } catch {
    & $NodeInfo.Path $Injector remove --port $Port *> $null
    throw "Theme injection failed; any partial injection was removed. Reason: $($_.Exception.Message)"
  }
  if (-not $injected.pass -or [string]$injected.payloadHash -ne $payloadHash) {
    & $NodeInfo.Path $Injector remove --port $Port *> $null
    throw 'Theme injection payload hash did not match the validated payload; the native interface was restored.'
  }
  try {
    $verified = Invoke-NodeJson $NodeInfo.Path $Injector @(
      'verify', '--theme-dir', [string]$resolved.directory, '--port', "$Port",
      '--timeout-ms', "$ColdStartRendererTimeoutMilliseconds"
    )
  } catch {
    & $NodeInfo.Path $Injector remove --port $Port *> $null
    throw "Theme verification failed; the native interface was restored. Reason: $($_.Exception.Message)"
  }
  if (-not $verified.pass -or [string]$verified.payloadHash -ne $payloadHash) {
    & $NodeInfo.Path $Injector remove --theme-dir $resolved.directory --port $Port *> $null
    throw 'Theme verification failed; the native interface was restored.'
  }

  $watcher = Start-ThemeWatcher $NodeInfo $resolved $payloadHash
  & $NodeInfo.Path $ThemeTool mark-loaded --state $StatePath --theme $resolved.id --hash $payloadHash
  if ($LASTEXITCODE -ne 0) { Stop-RecordedInjector; throw 'Could not record the loaded theme.' }
}

$install = $null
$nodeInfo = $null

$skillManifest = Assert-CanonicalSkillRoot

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
        skillRoot = $SkillRoot
        skillVersion = [string]$skillManifest.version
        canonicalSkill = $true
        runtimeIdentity = Get-RuntimeCanonicalStatus
        watchStatus = Get-WatchStatus
        nodeVersion = $null
        nodeRuntime = [ordered]@{
          status = 'NOT_READY'
          required = 'Validly signed Node.js 20+ with built-in WebSocket'
          source = $null
          path = $null
          version = $null
          webSocket = $false
          signer = $null
          reason = 'No trusted compatible Node.js runtime was found.'
        }
        stateRoot = $StateRoot
        runningPids = @((Get-CodexProcesses $install) | ForEach-Object Id)
        verifiedCdp = [bool](Test-VerifiedCdp $Port $install)
        liveCompatibilityProbe = $null
        themeState = $null
        launchers = @(Get-DesktopLauncherStatus)
        legacyDreamSkinLauncher = Get-LegacyDreamSkinLauncherStatus
      } | ConvertTo-Json -Depth 6
      break
    }
    $catalogResult = Invoke-NodeJson $nodeInfo.Path $ThemeTool @('validate', '--catalog', $Catalog)
    $compatibility = Get-CodexCompatibility $nodeInfo.Path $install
    $verifiedCdp = [bool](Test-VerifiedCdp $Port $install)
    $liveCompatibilityProbe = $null
    if ([string]$compatibility.status -eq 'UNSUPPORTED' -and $verifiedCdp) {
      try { $liveCompatibilityProbe = Invoke-NativeCompatibilityProbe $nodeInfo.Path $install }
      catch {
        $liveCompatibilityProbe = [pscustomobject]@{
          pass = $false
          status = 'UNSUPPORTED'
          mutationPerformed = $false
          reason = $_.Exception.Message
        }
      }
    }
    $themeState = Get-ThemeStatus $nodeInfo.Path
    [pscustomobject]@{
      pass = [bool]$catalogResult.pass
      status = 'READY'
      platform = 'Windows'
      codexVersion = [string]$install.Package.Version
      codexPath = $install.Exe
      skillRoot = $SkillRoot
      skillVersion = [string]$skillManifest.version
      canonicalSkill = $true
      compatibility = $compatibility
      runtimeContract = $catalogResult.runtimeContract
      runtimeIdentity = Get-RuntimeCanonicalStatus
      watchStatus = Get-WatchStatus
      nodeVersion = $nodeInfo.Version
      nodeRuntime = [ordered]@{
        status = $nodeInfo.Status
        required = 'Validly signed Node.js 20+ with built-in WebSocket'
        source = $nodeInfo.Source
        path = $nodeInfo.Path
        version = $nodeInfo.Version
        webSocket = $nodeInfo.WebSocket
        signer = $nodeInfo.Signer
      }
      stateRoot = $StateRoot
      runningPids = @((Get-CodexProcesses $install) | ForEach-Object Id)
      verifiedCdp = $verifiedCdp
      liveCompatibilityProbe = $liveCompatibilityProbe
      themeState = $themeState
      launchers = @(Get-DesktopLauncherStatus)
      legacyDreamSkinLauncher = Get-LegacyDreamSkinLauncherStatus
    } | ConvertTo-Json -Depth 12
  }

  'compatibility-audit' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    $compatibility = Get-CodexCompatibility $nodeInfo.Path $install
    if (-not (Test-VerifiedCdp $Port $install)) {
      [pscustomobject]@{
        pass = $false
        status = 'NOT_READY'
        mutationPerformed = $false
        platform = 'Windows'
        codexVersion = [string]$install.Package.Version
        compatibility = $compatibility
        reason = 'A verified running Codex loopback endpoint is required for the read-only native structure probe.'
      } | ConvertTo-Json -Depth 8
      break
    }
    $probe = Invoke-NativeCompatibilityProbe $nodeInfo.Path $install
    [pscustomobject]@{
      pass = [bool]$probe.pass
      status = [string]$probe.status
      mutationPerformed = $false
      platform = 'Windows'
      codexVersion = [string]$install.Package.Version
      compatibility = $compatibility
      probe = $probe
    } | ConvertTo-Json -Depth 12
  }

  { $_ -in @('prepare', 'switch') } {
    Invoke-WithThemeStateLock {
      if (-not $Theme) { throw "$Action requires -Theme <id>." }
      $install = Get-CodexInstall
      $nodeInfo = Get-Node $install
      New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
      & $nodeInfo.Path $ThemeTool select --catalog $Catalog --state $StatePath --theme $Theme
      if ($LASTEXITCODE -ne 0) { throw "Could not select theme: $Theme" }
      if ($CreateLaunchers) { Ensure-DesktopLaunchers $install }
    }
  }

  'start' {
    if (-not $AuthorizedRestart) { throw 'start requires -AuthorizedRestart after explicit current-turn authorization, or a deliberate Codex Themes launcher click.' }
    $startMutex = [Threading.Mutex]::new($false, (Get-ThemeStateMutexName))
    $startLockTaken = $false
    try {
      try { $startLockTaken = $startMutex.WaitOne(0) }
      catch [Threading.AbandonedMutexException] { $startLockTaken = $true }
      if (-not $startLockTaken) { throw 'Another Codex Themes start is already in progress; refusing a duplicate launch.' }
      $install = Get-CodexInstall
      $nodeInfo = Get-Node $install
      [void](Assert-CompatibleCodexBuild $nodeInfo.Path $install)
      if ($ShowThemeSelector) {
        $catalogResult = Invoke-NodeJson $nodeInfo.Path $ThemeTool @('validate', '--catalog', $Catalog)
        $model = New-ThemeSelectorModel -Catalog $catalogResult -State (Get-ThemeStatus $nodeInfo.Path)
        $restartTheme = {
          param($ThemeId)
          Restart-CodexForThemedLaunch $install
        }.GetNewClosure()
        $selectTheme = {
          param($ThemeId)
          New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
          & $nodeInfo.Path $ThemeTool select --catalog $Catalog --state $StatePath --theme $ThemeId
          if ($LASTEXITCODE -ne 0) { throw "Could not select theme: $ThemeId" }
        }.GetNewClosure()
        $startTheme = {
          param($ThemeId)
          Start-NextTheme $install $nodeInfo
        }.GetNewClosure()
        $selection = Invoke-ThemeSelectorWorkflow -Model $model `
          -ShowDialog { param($SelectorModel) Show-WindowsThemeSelectorDialog -Model $SelectorModel } `
          -BeforeSelect $restartTheme `
          -SelectTheme $selectTheme `
          -StartTheme $startTheme
        if ($selection.Status -eq 'cancelled') { Write-Host 'Theme selection cancelled; no state changed.' }
      } else {
        Start-NextTheme $install $nodeInfo -RestartExisting
      }
    } catch {
      if ($ShowThemeSelector) { Show-ThemeLauncherFailure $_.Exception.Message }
      throw
    } finally {
      if ($startLockTaken) { $startMutex.ReleaseMutex() }
      $startMutex.Dispose()
    }
  }

  'migrate' {
    Invoke-WithThemeStateLock {
      $install = Get-CodexInstall
      $nodeInfo = Get-Node $install
      $catalogResult = Invoke-NodeJson $nodeInfo.Path $ThemeTool @('validate', '--catalog', $Catalog)
      if (-not @($catalogResult.themes | Where-Object { $_.id -eq $LegacyDreamSkinTheme })) {
        throw "The migrated One Piece theme is missing from the validated catalog: $LegacyDreamSkinTheme"
      }
      Ensure-DesktopLaunchers $install
      $archivePath = Archive-LegacyDreamSkinLauncher
      if ($archivePath) {
        Write-Host "Codex Dream Skin was retired after its One Piece theme was migrated. Backup: $archivePath"
      } else {
        Write-Host 'No legacy Codex Dream Skin launcher was found.'
      }
    }
  }

  'verify' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    if (-not (Test-VerifiedCdp $Port $install)) { throw 'No verified Codex loopback endpoint is available.' }
    $resolved = Resolve-LoadedTheme $nodeInfo.Path
    & $nodeInfo.Path $Injector verify --theme-dir $resolved.directory --port $Port
    exit $LASTEXITCODE
  }

  'refresh' {
    Invoke-WithThemeStateLock {
      $install = Get-CodexInstall
      $nodeInfo = Get-Node $install
      [void](Assert-CompatibleCodexBuild $nodeInfo.Path $install)
      Refresh-LoadedTheme $install $nodeInfo | ConvertTo-Json -Depth 6
    }
  }

  'rollback' {
    Invoke-WithThemeStateLock {
      $install = Get-CodexInstall
      $nodeInfo = Get-Node $install
      $arguments = @('rollback', '--catalog', $Catalog, '--state', $StatePath)
      if ($Theme) { $arguments += @('--theme', $Theme) }
      & $nodeInfo.Path $ThemeTool @arguments
      if ($LASTEXITCODE -ne 0) { throw 'Could not stage the previous trusted theme for rollback.' }
    }
  }

  'restore' {
    Invoke-WithThemeStateLock {
      $install = Get-CodexInstall
      $nodeInfo = Get-Node $install
      $running = @(Get-CodexProcesses $install)
      $verifiedCdp = [bool](Test-VerifiedCdp $Port $install)
      if ($running.Count -gt 0 -and -not $verifiedCdp) {
        throw 'Codex is running without a verified theme endpoint; refusing to clear theme state or stop the recorded watcher.'
      }
      Stop-RecordedInjector
      if ($verifiedCdp) {
        & $nodeInfo.Path $Injector remove --port $Port
        if ($LASTEXITCODE -ne 0) { throw 'Could not remove the injected theme safely.' }
      }
      & $nodeInfo.Path $ThemeTool mark-restored --state $StatePath
      if ($LASTEXITCODE -ne 0) { throw 'Could not record restored state.' }
      Remove-Item -LiteralPath $RuntimePath -Force -ErrorAction SilentlyContinue
    }
  }

  'status' {
    $install = Get-CodexInstall
    $nodeInfo = Get-Node $install
    & $nodeInfo.Path $ThemeTool status --state $StatePath
    exit $LASTEXITCODE
  }
}
