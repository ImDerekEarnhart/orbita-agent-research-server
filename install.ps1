$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install .
& .\.venv\Scripts\orbita-agent.exe doctor

Write-Host "Orbita Agent Research Server is installed."
Write-Host "Run the demo: .\.venv\Scripts\orbita-agent.exe demo"
Write-Host "Run MCP:      .\.venv\Scripts\orbita-agent.exe serve --transport stdio"
