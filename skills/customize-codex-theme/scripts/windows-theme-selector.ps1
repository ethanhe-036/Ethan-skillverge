function New-ThemeSelectorModel {
  param(
    [Parameter(Mandatory)] [object]$Catalog,
    [Parameter(Mandatory)] [object]$State
  )

  if (-not $Catalog.pass) { throw 'The theme catalog has not passed validation.' }
  $themes = @($Catalog.themes)
  if ($themes.Count -lt 1) { throw 'The validated theme catalog is empty.' }

  $themeIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach ($theme in $themes) {
    if ([string]$theme.id -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
      throw "The validated catalog returned an invalid theme id: $($theme.id)"
    }
    if (-not $themeIds.Add([string]$theme.id)) {
      throw "The validated catalog returned a duplicate theme id: $($theme.id)"
    }
  }

  $declaredCollections = @($Catalog.collections)
  $collectionIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  $collectionDefinitions = @()
  foreach ($collection in $declaredCollections) {
    $collectionId = [string]$collection.id
    if ($collectionId -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
      throw "The validated catalog returned an invalid collection id: $collectionId"
    }
    if (-not $collectionIds.Add($collectionId)) {
      throw "The validated catalog returned a duplicate collection id: $collectionId"
    }
    if ([string]::IsNullOrWhiteSpace([string]$collection.name)) {
      throw "The validated catalog returned an unnamed collection: $collectionId"
    }
    $collectionDefinitions += [pscustomobject]@{
      Id = $collectionId
      Name = [string]$collection.name
      Summary = [string]$collection.summary
      Order = if ($null -ne $collection.order) { [int]$collection.order } else { 1000 }
    }
  }

  if ($collectionDefinitions.Count -eq 0) {
    [void]$collectionIds.Add('all-themes')
    $collectionDefinitions = @([pscustomobject]@{
      Id = 'all-themes'
      Name = '全部主题'
      Summary = '已通过验证的 Codex 主题。'
      Order = 1000
    })
  }

  $needsOther = @($themes | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.collectionId) }).Count -gt 0
  if ($needsOther -and $declaredCollections.Count -gt 0) {
    [void]$collectionIds.Add('other-themes')
    $collectionDefinitions += [pscustomobject]@{
      Id = 'other-themes'
      Name = '其他主题'
      Summary = '尚未归入系列的已验证主题。'
      Order = 10000
    }
  }

  $collections = @()
  foreach ($definition in @($collectionDefinitions | Sort-Object Order, Name)) {
    $members = @($themes | Where-Object {
      $themeCollectionId = [string]$_.collectionId
      if ([string]::IsNullOrWhiteSpace($themeCollectionId)) {
        $themeCollectionId = if ($declaredCollections.Count -eq 0) { 'all-themes' } else { 'other-themes' }
      }
      if (-not $collectionIds.Contains($themeCollectionId)) {
        throw "The validated catalog returned a theme with an unknown collection: $($_.id) -> $themeCollectionId"
      }
      $themeCollectionId -ceq $definition.Id
    })
    if ($members.Count -gt 0) {
      $collections += [pscustomobject]@{
        Id = $definition.Id
        Name = $definition.Name
        Summary = $definition.Summary
        Order = $definition.Order
        Themes = $members
      }
    }
  }

  # Validate collection references even when a bad collection has no matching definition.
  foreach ($theme in $themes) {
    $themeCollectionId = [string]$theme.collectionId
    if (-not [string]::IsNullOrWhiteSpace($themeCollectionId) -and
        -not $collectionIds.Contains($themeCollectionId)) {
      throw "The validated catalog returned a theme with an unknown collection: $($theme.id) -> $themeCollectionId"
    }
  }

  [pscustomobject]@{
    Themes = $themes
    Collections = $collections
    NextLaunchTheme = if ($State.nextLaunchTheme) { [string]$State.nextLaunchTheme } else { $null }
    LoadedTheme = if ($State.loadedTheme) { [string]$State.loadedTheme } else { $null }
  }
}

function Invoke-ThemeSelectorWorkflow {
  param(
    [Parameter(Mandatory)] [object]$Model,
    [Parameter(Mandatory)] [scriptblock]$ShowDialog,
    [scriptblock]$BeforeSelect,
    [Parameter(Mandatory)] [scriptblock]$SelectTheme,
    [Parameter(Mandatory)] [scriptblock]$StartTheme
  )

  $selectedTheme = & $ShowDialog $Model
  if ([string]::IsNullOrWhiteSpace([string]$selectedTheme)) {
    return [pscustomobject]@{ Status = 'cancelled'; Theme = $null }
  }
  $selectedTheme = [string]$selectedTheme
  if (@($Model.Themes | Where-Object { [string]$_.id -ceq $selectedTheme }).Count -ne 1) {
    throw "The selector returned a theme that is not in the validated catalog: $selectedTheme"
  }

  if ($BeforeSelect) { & $BeforeSelect $selectedTheme | Out-Null }
  & $SelectTheme $selectedTheme | Out-Null
  & $StartTheme $selectedTheme | Out-Null
  [pscustomobject]@{ Status = 'launched'; Theme = $selectedTheme }
}

function Show-WindowsThemeSelectorDialog {
  param([Parameter(Mandatory)] [object]$Model)

  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  [Windows.Forms.Application]::EnableVisualStyles()

  $form = [Windows.Forms.Form]::new()
  $form.Text = 'Codex Themes'
  $form.AccessibleName = 'Codex 主题选择器'
  $form.AccessibleDescription = '按系列浏览主题变体，并选择下次启动 Codex 时加载的主题。'
  $form.StartPosition = [Windows.Forms.FormStartPosition]::CenterScreen
  $form.FormBorderStyle = [Windows.Forms.FormBorderStyle]::FixedDialog
  $form.MaximizeBox = $false
  $form.MinimizeBox = $false
  $form.ShowInTaskbar = $true
  $form.ClientSize = [Drawing.Size]::new(980, 700)
  $form.AutoScaleMode = [Windows.Forms.AutoScaleMode]::Dpi
  $form.Font = [Drawing.Font]::new('Segoe UI', 10)
  $form.BackColor = [Drawing.Color]::FromArgb(244, 245, 246)

  $title = [Windows.Forms.Label]::new()
  $title.Text = 'Codex Themes'
  $title.Font = [Drawing.Font]::new('Segoe UI Semibold', 19)
  $title.ForeColor = [Drawing.Color]::FromArgb(27, 31, 35)
  $title.Location = [Drawing.Point]::new(24, 17)
  $title.Size = [Drawing.Size]::new(500, 36)
  $form.Controls.Add($title)

  $subtitle = [Windows.Forms.Label]::new()
  $subtitle.Text = '按系列选择视觉方向；变体只会在确认并启动后写入下次启动状态。'
  $subtitle.ForeColor = [Drawing.Color]::FromArgb(84, 91, 97)
  $subtitle.Location = [Drawing.Point]::new(26, 57)
  $subtitle.Size = [Drawing.Size]::new(920, 24)
  $form.Controls.Add($subtitle)

  $tree = [Windows.Forms.TreeView]::new()
  $tree.AccessibleName = '主题系列与变体'
  $tree.AccessibleDescription = '展开系列并选择一个主题变体。'
  $tree.Location = [Drawing.Point]::new(24, 94)
  $tree.Size = [Drawing.Size]::new(296, 528)
  $tree.BorderStyle = [Windows.Forms.BorderStyle]::FixedSingle
  $tree.BackColor = [Drawing.Color]::White
  $tree.ForeColor = [Drawing.Color]::FromArgb(36, 41, 45)
  $tree.FullRowSelect = $true
  $tree.HideSelection = $false
  $tree.ShowLines = $false
  $tree.ShowRootLines = $false
  $tree.ItemHeight = 30
  foreach ($collection in $Model.Collections) {
    $rootNode = [Windows.Forms.TreeNode]::new("$($collection.Name)  ·  $(@($collection.Themes).Count)")
    $rootNode.Name = [string]$collection.Id
    $rootNode.NodeFont = [Drawing.Font]::new('Segoe UI Semibold', 10)
    $rootNode.Tag = [pscustomobject]@{ Kind = 'collection'; Value = $collection }
    foreach ($theme in $collection.Themes) {
      $label = if ([string]::IsNullOrWhiteSpace([string]$theme.variantLabel)) {
        [string]$theme.name
      } else {
        [string]$theme.variantLabel
      }
      $themeNode = [Windows.Forms.TreeNode]::new($label)
      $themeNode.Name = [string]$theme.id
      $themeNode.Tag = [pscustomobject]@{ Kind = 'theme'; Value = $theme }
      [void]$rootNode.Nodes.Add($themeNode)
    }
    [void]$tree.Nodes.Add($rootNode)
    $rootNode.Expand()
  }
  $form.Controls.Add($tree)

  $details = [Windows.Forms.Panel]::new()
  $details.AccessibleName = '主题详情与预览'
  $details.Location = [Drawing.Point]::new(340, 94)
  $details.Size = [Drawing.Size]::new(616, 528)
  $details.BackColor = [Drawing.Color]::White
  $details.BorderStyle = [Windows.Forms.BorderStyle]::FixedSingle
  $form.Controls.Add($details)

  $themeName = [Windows.Forms.Label]::new()
  $themeName.Font = [Drawing.Font]::new('Segoe UI Semibold', 16)
  $themeName.ForeColor = [Drawing.Color]::FromArgb(28, 32, 36)
  $themeName.Location = [Drawing.Point]::new(20, 15)
  $themeName.Size = [Drawing.Size]::new(574, 32)
  $details.Controls.Add($themeName)

  $mode = [Windows.Forms.Label]::new()
  $mode.ForeColor = [Drawing.Color]::FromArgb(93, 100, 106)
  $mode.Location = [Drawing.Point]::new(22, 49)
  $mode.Size = [Drawing.Size]::new(570, 24)
  $details.Controls.Add($mode)

  $preview = [Windows.Forms.PictureBox]::new()
  $preview.AccessibleName = '主题工作台预览'
  $preview.Location = [Drawing.Point]::new(20, 80)
  $preview.Size = [Drawing.Size]::new(574, 250)
  $preview.BorderStyle = [Windows.Forms.BorderStyle]::FixedSingle
  $preview.BackColor = [Drawing.Color]::FromArgb(239, 240, 241)
  $preview.SizeMode = [Windows.Forms.PictureBoxSizeMode]::Zoom
  $details.Controls.Add($preview)

  $previewFallback = [Windows.Forms.Panel]::new()
  $previewFallback.AccessibleName = '主题色彩预览'
  $previewFallback.Location = $preview.Location
  $previewFallback.Size = $preview.Size
  $previewFallback.BorderStyle = [Windows.Forms.BorderStyle]::FixedSingle
  $previewFallback.BackColor = [Drawing.Color]::FromArgb(239, 240, 241)
  $previewFallback.Visible = $false
  $details.Controls.Add($previewFallback)

  $previewFallbackLabel = [Windows.Forms.Label]::new()
  $previewFallbackLabel.Text = '选择主题变体查看预览'
  $previewFallbackLabel.ForeColor = [Drawing.Color]::FromArgb(86, 93, 99)
  $previewFallbackLabel.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
  $previewFallbackLabel.Dock = [Windows.Forms.DockStyle]::Fill
  $previewFallback.Controls.Add($previewFallbackLabel)

  $summary = [Windows.Forms.Label]::new()
  $summary.ForeColor = [Drawing.Color]::FromArgb(52, 58, 63)
  $summary.Location = [Drawing.Point]::new(22, 342)
  $summary.Size = [Drawing.Size]::new(570, 48)
  $details.Controls.Add($summary)

  $swatchLabels = @('画布', '表面', '强调', '焦点')
  $swatchKeys = @('canvas', 'surface', 'accent', 'focus')
  $swatches = @()
  for ($index = 0; $index -lt 4; $index++) {
    $left = 22 + (142 * $index)
    $swatch = [Windows.Forms.Panel]::new()
    $swatch.AccessibleName = "$($swatchLabels[$index])色板"
    $swatch.Location = [Drawing.Point]::new($left, 399)
    $swatch.Size = [Drawing.Size]::new(126, 34)
    $swatch.BorderStyle = [Windows.Forms.BorderStyle]::FixedSingle
    $details.Controls.Add($swatch)
    $swatches += $swatch

    $label = [Windows.Forms.Label]::new()
    $label.Text = $swatchLabels[$index]
    $label.ForeColor = [Drawing.Color]::FromArgb(93, 100, 106)
    $label.TextAlign = [Drawing.ContentAlignment]::MiddleCenter
    $label.Location = [Drawing.Point]::new($left, 436)
    $label.Size = [Drawing.Size]::new(126, 20)
    $details.Controls.Add($label)
  }

  $pending = [Windows.Forms.Label]::new()
  $pending.AccessibleName = '已安排的下次启动主题'
  $pending.Location = [Drawing.Point]::new(22, 466)
  $pending.Size = [Drawing.Size]::new(570, 24)
  $pending.ForeColor = [Drawing.Color]::FromArgb(72, 78, 83)
  $details.Controls.Add($pending)

  $loaded = [Windows.Forms.Label]::new()
  $loaded.AccessibleName = '最近验证加载的主题'
  $loaded.Location = [Drawing.Point]::new(22, 494)
  $loaded.Size = [Drawing.Size]::new(570, 24)
  $loaded.ForeColor = [Drawing.Color]::FromArgb(72, 78, 83)
  $details.Controls.Add($loaded)

  $hint = [Windows.Forms.Label]::new()
  $hint.Text = '关闭或取消不会修改主题状态。系列节点仅用于浏览，必须选择具体变体。'
  $hint.Location = [Drawing.Point]::new(26, 636)
  $hint.Size = [Drawing.Size]::new(650, 24)
  $hint.ForeColor = [Drawing.Color]::FromArgb(104, 111, 116)
  $form.Controls.Add($hint)

  $cancel = [Windows.Forms.Button]::new()
  $cancel.Text = '取消'
  $cancel.AccessibleName = '取消主题选择'
  $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
  $cancel.Location = [Drawing.Point]::new(740, 642)
  $cancel.Size = [Drawing.Size]::new(100, 38)
  $form.Controls.Add($cancel)

  $launch = [Windows.Forms.Button]::new()
  $launch.Text = '选择并启动'
  $launch.AccessibleName = '选择主题并启动 Codex'
  $launch.DialogResult = [Windows.Forms.DialogResult]::OK
  $launch.Location = [Drawing.Point]::new(852, 642)
  $launch.Size = [Drawing.Size]::new(104, 38)
  $launch.Enabled = $false
  $form.Controls.Add($launch)
  $form.AcceptButton = $launch
  $form.CancelButton = $cancel

  $pendingTheme = @($Model.Themes | Where-Object { [string]$_.id -ceq [string]$Model.NextLaunchTheme }) | Select-Object -First 1
  $loadedTheme = @($Model.Themes | Where-Object { [string]$_.id -ceq [string]$Model.LoadedTheme }) | Select-Object -First 1
  $pending.Text = if ($pendingTheme) { "已安排下次启动：$($pendingTheme.name)" } else { '已安排下次启动：暂无' }
  $loaded.Text = if ($loadedTheme) { "最近验证加载：$($loadedTheme.name)" } else { '最近验证加载：暂无' }

  $clearPreview = {
    if ($preview.Image) {
      $oldImage = $preview.Image
      $preview.Image = $null
      $oldImage.Dispose()
    }
  }

  $showFallback = {
    param([Drawing.Color]$Background, [string]$Text)
    & $clearPreview
    $preview.Visible = $false
    $previewFallback.BackColor = $Background
    $previewFallbackLabel.Text = $Text
    $previewFallback.Visible = $true
    $previewFallback.BringToFront()
  }

  $update = {
    $tag = $tree.SelectedNode.Tag
    if (-not $tag -or [string]$tag.Kind -ne 'theme') {
      $collection = if ($tag) { $tag.Value } else { $null }
      $themeName.Text = if ($collection) { [string]$collection.Name } else { '选择一个主题系列' }
      $mode.Text = if ($collection) { "系列 · $(@($collection.Themes).Count) 个主题变体" } else { '系列浏览' }
      $summary.Text = if ($collection) { [string]$collection.Summary } else { '从左侧系列中选择一个具体主题变体。' }
      foreach ($swatch in $swatches) { $swatch.BackColor = [Drawing.Color]::FromArgb(236, 238, 240) }
      & $showFallback ([Drawing.Color]::FromArgb(239, 240, 241)) '选择下面的主题变体查看工作台预览'
      $launch.Enabled = $false
      return
    }

    $theme = $tag.Value
    $themeName.Text = [string]$theme.name
    $experimental = if ($theme.experimental) { ' · 实验性' } else { '' }
    $topology = if ($theme.presentation.artTopology) { [string]$theme.presentation.artTopology } else { 'native' }
    $presence = if ($theme.presentation.artPresence) { [string]$theme.presentation.artPresence } else { 'CSS-only' }
    $mode.Text = "模式：$($theme.mode) · 构图：$topology · $presence$experimental"
    $summary.Text = [string]$theme.summary
    for ($index = 0; $index -lt 4; $index++) {
      try { $swatches[$index].BackColor = [Drawing.ColorTranslator]::FromHtml([string]$theme.colors.($swatchKeys[$index])) }
      catch { $swatches[$index].BackColor = [Drawing.Color]::Transparent }
    }

    $previewPath = [string]$theme.presentation.previewPath
    if (-not [string]::IsNullOrWhiteSpace($previewPath) -and [IO.Path]::IsPathRooted($previewPath) -and
        (Test-Path -LiteralPath $previewPath -PathType Leaf)) {
      try {
        & $clearPreview
        $stream = [IO.File]::Open($previewPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
          $sourceImage = [Drawing.Image]::FromStream($stream)
          try { $preview.Image = [Drawing.Bitmap]::new($sourceImage) }
          finally { $sourceImage.Dispose() }
        } finally { $stream.Dispose() }
        $previewFallback.Visible = $false
        $preview.Visible = $true
        $preview.BringToFront()
      } catch {
        & $showFallback $swatches[0].BackColor '预览图暂时无法读取；主题色板仍可用'
      }
    } else {
      & $showFallback $swatches[0].BackColor 'CSS-only 主题 · 使用下方色板预览'
    }
    $launch.Enabled = $true
  }

  $tree.Add_AfterSelect($update)
  $tree.Add_NodeMouseDoubleClick({
    param($sender, $eventArgs)
    if ($eventArgs.Node.Tag -and [string]$eventArgs.Node.Tag.Kind -eq 'theme') {
      $tree.SelectedNode = $eventArgs.Node
      $form.DialogResult = [Windows.Forms.DialogResult]::OK
      $form.Close()
    }
  })

  $initialNode = $null
  $firstThemeNode = $null
  foreach ($rootNode in $tree.Nodes) {
    foreach ($themeNode in $rootNode.Nodes) {
      if (-not $firstThemeNode) { $firstThemeNode = $themeNode }
      if ([string]$themeNode.Name -ceq [string]$Model.NextLaunchTheme) { $initialNode = $themeNode }
    }
  }
  if (-not $initialNode) { $initialNode = $firstThemeNode }
  if ($initialNode) {
    $tree.SelectedNode = $initialNode
    $initialNode.EnsureVisible()
  }

  $result = $form.ShowDialog()
  $selectedNode = $tree.SelectedNode
  $selected = if ($result -eq [Windows.Forms.DialogResult]::OK -and $selectedNode.Tag -and
      [string]$selectedNode.Tag.Kind -eq 'theme') {
    [string]$selectedNode.Tag.Value.id
  } else {
    $null
  }
  & $clearPreview
  $form.Dispose()
  $selected
}
