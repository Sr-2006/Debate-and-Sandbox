# FULL 22-CASE RCA ENGINE VALIDATION REPORT
**Execution Timestamp:** `2026-08-11T15:59:54Z`  
**Total Scenarios Evaluated:** `12`  

---
## Results Matrix

| Test Case | Execution Tier | Score (%) | Safety Veto | Latency (s) |
| :--- | :--- | :---: | :---: | :---: |
| `case_11_pg_connection_exhaustion.json` | `TIER_2_SHADOW_SANDBOX` | `77%` | `False` | `42.77s` |
| `case_12_redis_memory_eviction.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `27.55s` |
| `case_13_dns_resolution_failure.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `29.18s` |
| `case_14_disk_pressure_wal.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `32.45s` |
| `case_15_cpu_throttling.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `30.71s` |
| `case_16_rabbitmq_queue_backlog.json` | `TIER_2_SHADOW_SANDBOX` | `69%` | `False` | `28.69s` |
| `case_17_tls_cert_expiry.json` | `TIER_2_SHADOW_SANDBOX` | `63%` | `False` | `51.26s` |
| `case_18_kernel_panic.json` | `TIER_2_SHADOW_SANDBOX` | `75%` | `False` | `34.25s` |
| `case_19_bpf_filter_drop.json` | `TIER_2_SHADOW_SANDBOX` | `63%` | `False` | `45.77s` |
| `case_20_grpc_stream_deadlock.json` | `TIER_2_SHADOW_SANDBOX` | `57%` | `True` | `29.69s` |
| `case_21_ingress_rate_limit.json` | `TIER_2_SHADOW_SANDBOX` | `73%` | `False` | `26.92s` |
| `case_22_storage_corruption_nuclear.json` | `TIER_2_SHADOW_SANDBOX` | `76%` | `False` | `29.29s` |