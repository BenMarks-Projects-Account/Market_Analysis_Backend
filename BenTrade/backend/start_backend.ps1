# start_backend.ps1
# Activate the project's virtualenv, install requirements, and start the FastAPI app.
# Usage: Open PowerShell, cd into backend, then: .\start_backend.ps1

$ErrorActionPreference = 'Stop'

# Ensure script runs from repository backend folder
Set-Location -Path (Join-Path $PSScriptRoot '.')

function Stop-StaleBackendProcesses {
    param(
        [int]$Port = 5000
    )

    Write-Host "Checking for stale backend listeners on port $Port..."

    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($conn in $listeners) {
        $pidToKill = $conn.OwningProcess
        if (-not $pidToKill) { continue }

        try {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidToKill" -ErrorAction SilentlyContinue
            $cmd = if ($proc) { $proc.CommandLine } else { '' }
            $name = if ($proc) { $proc.Name } else { '' }

            $isBackendLike = $false
            if ($cmd) {
                $isBackendLike = (
                    $cmd -match 'main\.py' -or
                    $cmd -match 'app\.main:app' -or
                    $cmd -match 'uvicorn' -or
                    $cmd -match 'start_backend\.ps1' -or
                    $cmd -match 'launcher\.py'
                )
            }

            if ($isBackendLike -or $name -eq 'python.exe' -or $name -eq 'pythonw.exe') {
                Write-Host "Stopping stale process PID=$pidToKill Name=$name"
                Start-Process -FilePath taskkill.exe -ArgumentList "/F /T /PID $pidToKill" -NoNewWindow -Wait | Out-Null
            } else {
                Write-Host "Port $Port in use by PID=$pidToKill (non-python/non-backend process). Leaving it untouched."
            }
        } catch {
            Write-Host "Failed to inspect/stop PID=$pidToKill : $($_.Exception.Message)"
        }
    }

    Start-Sleep -Milliseconds 700
    $remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($remaining.Count -gt 0) {
        $pids = ($remaining | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $Port is still in use (PID(s): $pids)."
    }
}

if (Test-Path ..\..\.venv\Scripts\Activate.ps1) {
    $VenvActivate = "..\..\.venv\Scripts\Activate.ps1"
} elseif (Test-Path .\.venv\Scripts\Activate.ps1) {
    $VenvActivate = ".\.venv\Scripts\Activate.ps1"
} elseif (Test-Path .\venv\Scripts\Activate.ps1) {
    $VenvActivate = ".\venv\Scripts\Activate.ps1"
} else {
    Write-Host "Virtualenv not found. Creating one at .\.venv ..."
    python -m venv .venv
    $VenvActivate = ".\.venv\Scripts\Activate.ps1"
}

Write-Host "Activating virtualenv..."
& $VenvActivate

Write-Host "Upgrading pip and installing requirements (this may take a moment)..."
python -m pip install --upgrade pip
pip install -r requirements.txt

Stop-StaleBackendProcesses -Port 5000

Write-Host "Starting FastAPI app (uvicorn app.main:app) on port 5000..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 5000
