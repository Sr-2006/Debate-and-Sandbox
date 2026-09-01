# Cloudflare Tunnel Federation & Data Sharing Guide — PHASE 3 (Updated)

> **This is the Phase 3 (Teammate / Laptop C) edition.**
> The original host-centric guide is preserved unchanged in
> `cloudflare_usage_gd.md` for reference. This version rewrites the teammate
> sections around Phase 3's actual job: **pull Phase 1 evidence over the tunnel,
> assemble it for the debate engine, run the debate, and publish the conclusion
> to Phase 4.**

This guide contains step-by-step instructions to establish the tunnel connection,
sync `frontend_data`, assemble `debate_evidence/`, and expose Phase 3 services —
all without paid accounts or port forwarding.

---

## Part 1: Host Guide (Laptop A) — reference only

> Phase 3 is a **teammate**, not the host. This section is kept for context so
> you understand what the host is serving. Full details in the original guide.

### Host setup (summary)
```powershell
cd C:\Users\sujay\Downloads\complex\auto-sre-platform
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
winget install --id Cloudflare.cloudflared -e
```

### Host serves telemetry
```powershell
# Generate fresh dataset & JSON files inside frontend_data/
python package_ml_dataset.py

# Terminal 1: local dataset server
python federation/serve_dataset.py --port 8090

# Terminal 2: Cloudflare tunnel
.\federation\start-tunnel.ps1 -Phase phase1
```

The host's public URL appears in Terminal 2:
```
https://xxxx-xxxx-xxxx-xxxx.trycloudflare.com
```
and is committed to `federation/.env.federation` as `PHASE1_DATASET_URL`.

### Host diagnostics
```powershell
Get-NetTCPConnection -LocalPort 8090 -State Listen
python federation/test_phase1_url.py
```

---

## Part 2: Phase 3 Teammate Guide (Laptop C) — PRIMARY

### 1. Setup from scratch
```powershell
# Clone / navigate to the debate engine
cd C:\Users\sujay\Downloads\debate\multi-agent-debate

# Python environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Cloudflare tunnel connector (needed to expose Phase 3 services)
winget install --id Cloudflare.cloudflared -e
```

> **Ollama must be running locally** on `localhost:11434` with the worker and
> orchestrator models pulled. The debate loop never goes through the tunnel.

---

### 2. Get the Phase 1 host URL
```powershell
# From git (host commits it)
git pull
Get-Content federation\.env.federation | Select-String "PHASE1_DATASET_URL"

# Store for the session
$HOST_URL = "https://xxxx-xxxx-xxxx-xxxx.trycloudflare.com"   # ← replace
```

---

### 3. Verify connection to the host
```powershell
# Built-in tester
python federation/test_phase1_url.py --url $HOST_URL

# Or directly
Invoke-RestMethod "$HOST_URL/health"
Invoke-RestMethod "$HOST_URL/files"
```

---

### 4. Retrieve & sync evidence (the Phase 3 way)

**Preferred — one command does download + assembly:**
```powershell
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL
```
This downloads every hosted JSON into `frontend_data/`, then assembles
`debate_evidence/<incident_id>/` folders in the exact layout the
`EvidenceLoader` reads.

**Variants:**
```powershell
# Only the hottest N incidents
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL -Top 5

# A specific incident
.\scripts\sync_evidence.ps1 -HostUrl $HOST_URL -IncidentId "payment-service_14"
```

**Manual single-file downloads (if you only need one artifact):**
```powershell
Invoke-WebRequest -Uri "$HOST_URL/dataset"               -OutFile "frontend_data\unified_master_dataset.json"
Invoke-WebRequest -Uri "$HOST_URL/status.json"           -OutFile "frontend_data\status.json"
Invoke-WebRequest -Uri "$HOST_URL/time_series.json"      -OutFile "frontend_data\time_series.json"
Invoke-WebRequest -Uri "$HOST_URL/events_and_incidents.json" -OutFile "frontend_data\events_and_incidents.json"
Invoke-WebRequest -Uri "$HOST_URL/chaos_history.json"    -OutFile "frontend_data\chaos_history.json"
```

**Download the entire `frontend_data` folder (raw one-liner):**
```powershell
New-Item -ItemType Directory -Force -Path "frontend_data" ; (Invoke-RestMethod "$HOST_URL/files") | ForEach-Object { Invoke-WebRequest -Uri "$HOST_URL/$_" -OutFile "frontend_data/$_" ; Write-Host "Downloaded $_" -ForegroundColor Green }
```

---

### 5. Run the debate on synced evidence
```powershell
# Hottest incident from the dataset
python run_from_evidence.py --dataset frontend_data/unified_master_dataset.json

# Specific incident from its assembled folder
python run_from_evidence.py --folder debate_evidence/payment-service_14

# Publish the conclusion to Phase 4 (RabbitMQ, or offline file fallback)
python run_from_evidence.py --folder debate_evidence/payment-service_14 --publish
```

---

### 6. Expose Phase 3 services back to the team

```powershell
# MCP SSE server (port 8001) — for Fact Checker / external tool calls
.\federation\start-tunnel.ps1 -Phase phase3

# Ollama API (port 11434) — ONLY if another laptop needs it
.\federation\start-tunnel.ps1 -Phase phase3 -Port 11434
```

> ⚠️ **Ollama stays local for the debate loop.** The engine calls
> `localhost:11434` directly. The tunnel is only for external MCP consumers.

---

### 7. Register Phase 3 URLs in Git
After starting a tunnel and getting a URL:
1. Open `federation/.env.federation`
2. Add/update:
   ```ini
   PHASE3_MCP_URL=https://...
   PHASE3_OLLAMA_URL=https://...
   ```
3. Commit and push:
   ```powershell
   git add federation/.env.federation
   git commit -m "federation: update phase3 tunnel URLs"
   git push
   ```
4. Teammates run `git pull` to receive updated endpoints.

---

## Part 3: Phase 3 operational constraints

1. **Ollama stays local** — the debate loop calls `localhost:11434`
   (`qwen2.5:3b` worker / `qwen2.5:7b` orchestrator) only; never tunneled.
2. **Output contract** — the conclusion is published as `autosre.action.proposed`
   to RabbitMQ for Phase 4. If a proposed command trips the semantic veto
   (cosine ≥ 0.82 against `FORBIDDEN_CENTROIDS`), confidence is capped at 64%
   and it routes to human review.
3. **Agents** — Optimist, Critic, Fact Checker, Orchestrator; the Orchestrator
   synthesizes consensus into a runbook + remediation command.
4. **Slow path is inference, not transport** — evidence sync is sub-second;
   budget seconds–minutes for the debate rounds themselves.

---

## Part 4: Phase 3 troubleshooting

| Symptom | Fix |
|---------|-----|
| `Invoke-RestMethod` times out | Tunnel still propagating — wait 60 s, retry |
| `/files` returns empty list | Host hasn't run `package_ml_dataset.py` yet |
| `incident_context.json` missing blocks | Dataset schema mismatch — check `metadata.dataset_version` |
| `metrics_snapshot` is `[]` | Normal for fresh clusters — the loader handles it |
| `log_cluster_template` is blank | Upstream Drain3 issue — flag to Phase 1 |
| Sync script can't parse dataset | Validate JSON: `python -m json.tool frontend_data\unified_master_dataset.json` |
| Debate can't reach Ollama | Ensure Ollama is running locally on `localhost:11434` |
| Publish falls back to file | RabbitMQ/pika unavailable — message written to `output/proposed_actions/` |

---

## Part 5: Contract guarantees Phase 3 relies on (from Phase 1)

- `incidents[]` is pre-sorted descending by `priority_score` (index 0 = hottest)
- `incident_id` always matches `^[a-zA-Z0-9_-]+_\d+$`
- `log_cluster_template` is never blank and never a bare `at <*>` fragment
- `metrics_snapshot` may be `[]` for fresh clusters
- `metadata` carries `dataset_version` and `git_sha`
- Log samples have `trace_id`/`span_id` populated ≥ 95% of the time
