$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Security gate command failed with exit code $LASTEXITCODE"
    }
}

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    Invoke-Checked { python -m unittest discover -s tests -v }
    Invoke-Checked { python -m unittest discover -s services/messenger/tests -v }
    Invoke-Checked { node --check static/messenger/js/client.js }
    Invoke-Checked { python -m pip_audit --requirement requirements.txt }
    Invoke-Checked { python -m pip_audit --requirement services/messenger/requirements.txt }
    Invoke-Checked {
        python -m bandit -r services/messenger/messenger app.py auth.py auth_ldap.py -ll
    }
    Invoke-Checked { docker compose config --quiet }
    Invoke-Checked { docker compose build messenger-api }
    Invoke-Checked {
        docker scout cves universlaai-messenger-api:latest `
            --only-severity critical,high --exit-code
    }
    Write-Host 'Security gate passed.'
}
finally {
    Pop-Location
}
