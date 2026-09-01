#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Configures the local Ollama server for true parallel agent inference.

.DESCRIPTION
  Ollama serves one request per model slot by default, which serializes the
  three debate agents and is the primary cause of the 47-94s pipeline latency.
  This script sets OLLAMA_NUM_PARALLEL=3 (and a VRAM-friendly default ctx) as a
  persistent user environment variable, then restarts the Ollama server.

  Run once, then start Ollama normally. Verify with:
      ollama ps
#>

Write-Host "[setup] Setting OLLAMA_NUM_PARALLEL=3 (persistent user env var)..." -ForegroundColor Cyan
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "3", "User")

# Keep the model loaded between requests to avoid reload penalties.
Write-Host "[setup] Setting OLLAMA_KEEP_ALIVE=30m ..." -ForegroundColor Cyan
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "30m", "User")

# Optional: cap loaded models to 1 to protect the RTX 3050's VRAM.
Write-Host "[setup] Setting OLLAMA_MAX_LOADED_MODELS=1 ..." -ForegroundColor Cyan
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")

Write-Host ""
Write-Host "[setup] Done. Restart the Ollama server for changes to take effect:" -ForegroundColor Green
Write-Host "    1. Quit Ollama from the system tray (or: Get-Process ollama | Stop-Process)"
Write-Host "    2. Relaunch:  ollama serve"
Write-Host ""
Write-Host "[setup] Ensure the model is pulled:" -ForegroundColor Green
Write-Host "    ollama pull qwen2.5:3b"
