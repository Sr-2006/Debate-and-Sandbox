# FULL 22-CASE RCA ENGINE VALIDATION REPORT
**Execution Timestamp:** `2026-08-11T16:08:33Z`  
**Total Scenarios Evaluated:** `12`  

---
## Results Matrix

| Test Case | Execution Tier | Score (%) | Safety Veto | Latency (s) |
| :--- | :--- | :---: | :---: | :---: |
| `case_11_pg_connection_exhaustion.json` | `TIER_2_SHADOW_SANDBOX` | `63%` | `False` | `25.2s` |
| `case_12_redis_memory_eviction.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `13.49s` |
| `case_13_dns_resolution_failure.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `11.57s` |
| `case_14_disk_pressure_wal.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `13.89s` |
| `case_15_cpu_throttling.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `11.81s` |
| `case_16_rabbitmq_queue_backlog.json` | `TIER_2_SHADOW_SANDBOX` | `73%` | `False` | `19.42s` |
| `case_17_tls_cert_expiry.json` | `TIER_2_SHADOW_SANDBOX` | `72%` | `False` | `17.53s` |
| `case_18_kernel_panic.json` | `TIER_1_AUTONOMOUS_EXECUTION` | `88%` | `False` | `12.73s` |
| `case_19_bpf_filter_drop.json` | `TIER_2_SHADOW_SANDBOX` | `63%` | `False` | `18.28s` |
| `case_20_grpc_stream_deadlock.json` | `TIER_2_SHADOW_SANDBOX` | `83%` | `False` | `11.96s` |
| `case_21_ingress_rate_limit.json` | `TIER_2_SHADOW_SANDBOX` | `73%` | `False` | `12.3s` |
| `case_22_storage_corruption_nuclear.json` | `TIER_2_SHADOW_SANDBOX` | `61%` | `True` | `14.02s` |