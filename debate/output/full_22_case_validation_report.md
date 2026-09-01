# FULL 22-CASE RCA ENGINE VALIDATION REPORT
**Execution Timestamp:** `2026-09-01T21:28:49Z`  
**Total Scenarios Evaluated:** `22`  

---
## Results Matrix

| Test Case | Execution Tier | Score (%) | Safety Veto | Latency (s) |
| :--- | :--- | :---: | :---: | :---: |
| `case_01_semantic_consensus.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `40.01s` |
| `case_02_safety_veto.json` | `TIER_2_SHADOW_SANDBOX` | `50%` | `True` | `37.71s` |
| `case_03_guided_pivot.json` | `TIER_2_SHADOW_SANDBOX` | `73%` | `False` | `39.24s` |
| `case_04_selective_resample.json` | `TIER_2_SHADOW_SANDBOX` | `63%` | `False` | `75.99s` |
| `case_05_mttr_decay.json` | `TIER_2_SHADOW_SANDBOX` | `70%` | `False` | `37.06s` |
| `case_06_component_divergence.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `39.3s` |
| `case_07_evidence_gap.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `37.71s` |
| `case_08_schema_violation.json` | `TIER_2_SHADOW_SANDBOX` | `48%` | `False` | `72.18s` |
| `case_09_borderline_route.json` | `TIER_2_SHADOW_SANDBOX` | `77%` | `False` | `37.33s` |
| `case_10_deep_failure.json` | `TIER_2_SHADOW_SANDBOX` | `65%` | `False` | `37.57s` |
| `case_11_pg_connection_exhaustion.json` | `TIER_2_SHADOW_SANDBOX` | `68%` | `False` | `36.61s` |
| `case_12_redis_memory_eviction.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `39.54s` |
| `case_13_dns_resolution_failure.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `39.35s` |
| `case_14_disk_pressure_wal.json` | `TIER_2_SHADOW_SANDBOX` | `65%` | `False` | `41.83s` |
| `case_15_cpu_throttling.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `39.15s` |
| `case_16_rabbitmq_queue_backlog.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `38.29s` |
| `case_17_tls_cert_expiry.json` | `TIER_2_SHADOW_SANDBOX` | `78%` | `False` | `41.42s` |
| `case_18_kernel_panic.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `85%` | `False` | `46.08s` |
| `case_19_bpf_filter_drop.json` | `TIER_2_SHADOW_SANDBOX` | `65%` | `False` | `41.09s` |
| `case_20_grpc_stream_deadlock.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `38.22s` |
| `case_21_ingress_rate_limit.json` | `TIER_2_SHADOW_SANDBOX` | `65%` | `False` | `41.02s` |
| `case_22_storage_corruption_nuclear.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `37.95s` |