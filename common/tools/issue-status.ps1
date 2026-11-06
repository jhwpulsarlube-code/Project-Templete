[CmdletBinding()]
param(
    # 지정하면 종료(done, wontfix) 티켓도 출력한다. 기본은 제외.
    [switch]$All
)

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$scratchRoot = Join-Path $repoRoot '.scratch'

if (-not (Test-Path -LiteralPath $scratchRoot -PathType Container))
{
    throw "Issue tracker directory was not found: $scratchRoot"
}

$actionableStatuses = @('needs-triage', 'ready-for-agent', 'ready-for-human')
$waitingStatuses = @('needs-info')
$closedStatuses = @('done', 'wontfix')

$tickets = @(Get-ChildItem -LiteralPath $scratchRoot -Recurse -File -Filter '*.md' |
    Where-Object {
        $relativeDirectory = $_.DirectoryName.Substring($scratchRoot.Length).TrimStart('\', '/')
        $relativeDirectory -split '[\\/]' -contains 'issues'
    } |
    ForEach-Object {
        $status = 'missing'
        $title = ''
        # foreach 안의 break는 ReadLines 열거자를 Dispose하지 않아 GC 전까지 핸들이 남는다.
        # 편집 도구가 "접근할 수 없다"로 실패하므로 리더를 직접 닫고 FileShare.ReadWrite로 연다.
        $stream = [System.IO.File]::Open($_.FullName, 'Open', 'Read', 'ReadWrite, Delete')
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, $true)
        try
        {
            while ($null -ne ($line = $reader.ReadLine()))
            {
                $trimmedLine = $line.Trim()
                if ($title -eq '' -and $trimmedLine -match '^#\s+(.+)$')
                {
                    $title = $Matches[1].Trim()
                }
                if ($trimmedLine -match '^(?:\*\*Status:\*\*|Status:)\s*(.*?)\s*(?:\*\*)?$')
                {
                    # "done (2026-09-02 — ...)" 같은 부연 설명은 잘라 상태값만 남긴다.
                    $status = ($Matches[1] -split '\s+[(—–-]', 2)[0].Trim()
                    if ($status -eq '') { $status = 'missing' }
                    break
                }
            }
        }
        finally
        {
            $reader.Dispose()
            $stream.Dispose()
        }

        $group = if ($actionableStatuses -contains $status) {
            '작업 가능'
        } elseif ($waitingStatuses -contains $status) {
            '응답 대기'
        } elseif ($closedStatuses -contains $status) {
            '종료'
        } else {
            '기타'
        }

        $relativePath = $_.FullName.Substring($scratchRoot.Length).TrimStart('\', '/') -replace '/', '\' -replace '\\issues\\', '\'
        if ($title -eq '') { $title = $_.BaseName }
        [pscustomobject]@{
            Group  = $group
            Status = $status
            Ticket = $relativePath
            Title  = $title
            Sort   = if ($actionableStatuses -contains $status) { 0 }
                     elseif ($waitingStatuses -contains $status) { 1 }
                     elseif ($closedStatuses -contains $status) { 3 }
                     else { 2 }
        }
    })

if ($tickets.Count -eq 0)
{
    Write-Output 'No issue files found under .scratch.'
    exit 0
}

$shown = if ($All) { $tickets } else { @($tickets | Where-Object { $closedStatuses -notcontains $_.Status }) }

if ($shown.Count -gt 0)
{
    $shown |
        Sort-Object Sort, Status, Ticket |
        Select-Object Group, Status, Ticket, Title |
        Format-Table -AutoSize -Wrap
}

if ($All)
{
    Write-Output ("`n총 {0}개 티켓" -f $tickets.Count)
}
else
{
    Write-Output ("`n총 {0}개 티켓 (종료 {1}개 제외; 포함하려면 -All)" -f $shown.Count, ($tickets.Count - $shown.Count))
}
