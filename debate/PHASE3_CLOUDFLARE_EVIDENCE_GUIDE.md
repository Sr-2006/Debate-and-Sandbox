# Phase 3 — Cloudflare Evidence Collection Guide

> **Role**: Teammate (Laptop C) — NOT the host.
> **Purpose**: Pull Phase 1 telemetry from the host tunnel, assemble it into
> `debate_evidence/<incident_id>/` folders, and keep them synced so the debate
> engine always has fresh evidence to argue over.

---

## 0. Prerequisites (one-time)

```powershell
# From the debate engine root
cd C:\Users\sujay\Downloads\debate\multi-agent-debate

# Python env (if not already set up)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Cloudflare tunnel client (needed only if YOU also expose Phase 3 services)
winget install --id Cloudflare.cloudflared -e
```

---

## 1. Get the Host URL

The Phase 1 host (Laptop A) publishes its tunnel URL in `federation/.env.federation`.

```powershell
# Option A: pull from git (if the host committed it)
git pull
Get-Content federation\.env.federation | Select-String "PHASE1_DATASET_URL"

# Option B: the host tells you directly, e.g.:
#   https://tokyo-equivalent-reduce-integrating.trycloudflare.com
```

Store it for the session:

```powershell
$HOST_URL = "https://xxxx-xxxx-xxxx-xxxx.trycloudflare.com"   # ← replace
```

---

## 2. Verify Connectivity

```powershell
# Health check — should return JSON with status ok
Invoke-RestMethod "$HOST_URL/health"

# List available files
Invoke-RestMethod "$HOST_URL/files"
```

If the health check fails, the tunnel may still be propagating (wait 30–60 s)
or the host terminal may have stopped.

---

## 3. One-Shot Full Sync (recommended first run)

The sync script downloads every file the host exposes, caches them locally in
`frontend_data/`, then assembles per-incident evidence folders.

```powershell
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL
```

What it does, step by step:

| Step | Action |
|------|--------|
| 1 | `GET /files` → list of hosted JSON filenames |
| 2 | Downloads each file into `frontend_data/` (local cache) |
| 3 | Parses `unified_master_dataset.json` → iterates `incidents[]` |
| 4 | For each incident, creates `debate_evidence/<incident_id>/` with the exact layout the `EvidenceLoader` expects |
| 5 | Filters `time_series.json` to the target service + its dependencies |
| 6 | Prints a summary: incidents assembled, warnings, file sizes |

---

## 4. Periodic Re-Sync

Phase 1 regenerates telemetry after each chaos cycle. Re-run the same command
to refresh:

```powershell
# Full refresh (overwrites existing evidence folders)
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL

# Refresh only the hottest N incidents (faster)
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL -Top 5

# Refresh a specific incident
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL -IncidentId "payment-service_14"
```

Suggested cadence: **after every chaos injection cycle** or whenever the host
announces a dataset regeneration (`python package_ml_dataset.py`).

---

## 5. Manual Single-File Downloads

If you only need one artifact:

```powershell
# Master dataset
Invoke-WebRequest -Uri "$HOST_URL/dataset" -OutFile "frontend_data\unified_master_dataset.json"

# Status / health
Invoke-WebRequest -Uri "$HOST_URL/status.json" -OutFile "frontend_data\status.json"

# Time series
Invoke-WebRequest -Uri "$HOST_URL/time_series.json" -OutFile "frontend_data\time_series.json"

# Events & incidents (raw logs)
Invoke-WebRequest -Uri "$HOST_URL/events_and_incidents.json" -OutFile "frontend_data\events_and_incidents.json"

# Chaos history
Invoke-WebRequest -Uri "$HOST_URL/chaos_history.json" -OutFile "frontend_data\chaos_history.json"
```

---

## 6. What Gets Assembled (folder layout)

After sync, `debate_evidence/` looks like this:

```
debate_evidence/
└── payment-service_14/
    ├── incident_context.json        # full 6-block entry from the dataset
    ├── system_context.json          # health score + active warnings
    ├── topology.json                # role, dependencies, ports
    ├── chaos_context.json           # active mutations (or empty)
    ├── logs/
    │   ├── cluster_template.txt     # Drain3 template (plain text)
    │   └── samples.json             # up to 5 unmasked log samples
    ├── metrics/
    │   ├── snapshot.json            # up to 3 metric points
    │   └── time_series.json         # filtered to target + deps
    └── similar_incidents.json       # Phase 2 ChromaDB matches (if available)
```

This is the **exact** layout `EvidenceLoader.load_from_folder()` reads.

---

## 7. Run the Debate on Synced Evidence

```powershell
# Hottest incident from the dataset
python run_from_evidence.py --dataset frontend_data/unified_master_dataset.json

# Specific incident from its evidence folder
python run_from_evidence.py --folder debate_evidence/payment-service_14

# Publish the conclusion to Phase 4 (RabbitMQ or offline file)
python run_from_evidence.py --folder debate_evidence/payment-service_14 --publish
```

---

## 8. Exposing Phase 3 Back to the Team (optional)

When the debate engine needs to be reachable by Phase 4 or for MCP tool calls:

```powershell
# Expose the MCP SSE server (port 8001)
.\federation\start-tunnel.ps1 -Phase phase3

# Expose Ollama (port 11434) — ONLY if another laptop needs it
.\federation\start-tunnel.ps1 -Phase phase3 -Port 11434
```

Then update `federation/.env.federation`:

```ini
PHASE3_MCP_URL=https://your-phase3-url.trycloudflare.com
PHASE3_OLLAMA_URL=https://your-ollama-url.trycloudflare.com
```

> ⚠️ **Ollama stays local for the debate loop.** The engine calls
> `localhost:11434` directly. The tunnel is only for external MCP consumers.

---

## 9. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Invoke-RestMethod` times out | Tunnel still propagating — wait 60 s, retry |
| `/files` returns empty list | Host hasn't run `package_ml_dataset.py` yet |
| `incident_context.json` missing blocks | Dataset schema mismatch — check `metadata.dataset_version` |
| `metrics_snapshot` is `[]` | Normal for fresh clusters — the loader handles it |
| `log_cluster_template` is blank | Upstream Drain3 issue — flag to Phase 1 |
| Sync script can't parse dataset | Ensure the file is valid JSON: `python -m json.tool frontend_data\unified_master_dataset.json` |

---

## 10. Contract Guarantees (from Phase 1)

These are **guaranteed** by the upstream pipeline — the engine relies on them:

- `incidents[]` is pre-sorted descending by `priority_score` (index 0 = hottest)
- `incident_id` always matches `^[a-zA-Z0-9_-]+_\d+$`
- `log_cluster_template` is never blank and never a bare `at <*>` fragment
- `metrics_snapshot` may be `[]` for fresh clusters
- `metadata` carries `dataset_version` and `git_sha`
- Log samples have `trace_id`/`span_id` populated ≥ 95% of the time
