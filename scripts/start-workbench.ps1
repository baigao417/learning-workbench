[CmdletBinding()]
param(
    [string]$Source,
    [string]$State,
    [ValidateRange(0, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

try {
    Set-Location -LiteralPath $projectRoot
    if ([string]::IsNullOrWhiteSpace($State)) {
        $State = Join-Path $projectRoot '.local\state'
    }

    if ([string]::IsNullOrWhiteSpace($Source)) {
        # Machine-local default course folder; .local/ is git-ignored.
        $sourceFile = Join-Path $projectRoot '.local\start-source.txt'
        if (Test-Path -LiteralPath $sourceFile -PathType Leaf) {
            $Source = (Get-Content -LiteralPath $sourceFile -Encoding UTF8 -TotalCount 1).Trim()
        }
    }
    if ([string]::IsNullOrWhiteSpace($Source)) {
        throw 'No course folder given. Pass -Source <folder> or put the folder path in .local\start-source.txt'
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Course folder was not found: $Source"
    }

    $python = Get-Command python.exe -CommandType Application -ErrorAction Stop |
        Select-Object -First 1 -ExpandProperty Source
    & $python --version
    if ($LASTEXITCODE -ne 0) {
        throw 'Python could not report its version.'
    }

    if ($Check) {
        Write-Output 'Launcher check passed.'
        Write-Output "Source: $Source"
        Write-Output "Python: $python"
        exit 0
    }

    $arguments = @(
        '-m', 'learning_workbench', 'start',
        '--source', $Source,
        '--state', $State,
        '--port', $Port
    )
    if ($NoBrowser) {
        $arguments += '--no-browser'
    }

    & $python @arguments
    exit $LASTEXITCODE
}
catch {
    Write-Error "Learning Workbench could not start: $($_.Exception.Message)"
    exit 1
}
