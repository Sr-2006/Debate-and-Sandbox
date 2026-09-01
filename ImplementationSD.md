# Phase 3 Debate and Phase 4 Shadow Sandbox: Final Implementation Plan

## 0. Document control

- Repository: `Sr-2006/Debate-and-Sandbox`
- Audited branch: `main`
- Audited commit: `2a3867c14af99d003ec8cecd044a01ef874346b8`
- Scope: Phase 3 debate engine, Phase 3-to-4 contract, Phase 4 shadow sandbox, and their verification/operational surfaces.
- Deliverable type: implementation instructions only. This document intentionally contains no implementation code.
- Primary objectives:
  1. Eliminate avoidable `BLOCKED_UNMAPPED` outcomes without weakening safety.
  2. Replace the arbitrary low-confidence calculation with an evidence-based, calibrated execution-confidence model.
  3. Close unnoticed correctness, safety, observability, testing, and integration gaps before connecting the RL engine.

## 0.1 Final review of the submitted draft

The submitted `Pasted markdown(3).md` is directionally correct but is **not implementation-ready without the changes below**. These are mandatory, not optional refinements:

1. Correct the report-generator path to `Arse_shadow/shadow_sandbox/reports/report_generator.py`. There is no audited `Arse_shadow/shadow_sandbox/report_generator.py`.
2. Do not merely “modify `tools.py`.” Create operation-specific executor and verifier modules, leave a compatibility facade temporarily, then delete the raw-command functions after callers migrate.
3. Do not ask the LLM to construct the transport envelope. The model emits a schema-validated diagnosis and typed intent proposal; deterministic application code adds IDs, hashes, source versions, timestamps, routing metadata, and signatures.
4. Do not promise zero `BLOCKED_UNMAPPED` for every imaginable input. The release target is zero **avoidable** unmapped results in the 22-case pack. Genuine gaps must become `BLOCKED_UNKNOWN_CAPABILITY` or `UNSUPPORTED_ENVIRONMENT_CAPABILITY` with a structured gap record.
5. Define one authoritative delivery path per deployment. The current “folder plus RabbitMQ plus offline fallback” behavior can duplicate mutations.
6. Define durable idempotency and state ownership. An in-memory filename set is insufficient.
7. Define how a new capability becomes trusted before historical confidence exists. Otherwise the redesign either invents a prior score again or blocks every new mutation forever.
8. Preserve safety-veto precedence. Confidence must never override schema, target, attestation, policy, approval, or destructive-action gates.
9. Separate `DIAGNOSED`, `MUTATION_APPLIED_UNVERIFIED`, and `VERIFIED_RECOVERED`. The submitted draft still focuses too heavily on a single success state.
10. Add exact expected changes, tests, and exit criteria per ticket so the coding agent does not make architectural decisions while implementing.

The remainder of this document is the final, corrected plan and supersedes the submitted draft.

## 0.2 Fixed implementation decisions

Use these decisions unless a test proves they cannot work in the audited repository:

- Contract version: semantic version `2.0.0`.
- Event type: `autosre.action.proposed`; RabbitMQ routing key: `autosre.action.proposed.v2`.
- Validation: one shared Python contract package plus authoritative JSON Schema; no duplicated hand-written dictionaries.
- Primary transport: RabbitMQ when configured. File transport is a durable outbox/fallback, never a simultaneous second delivery.
- Local durability: SQLite in WAL mode for inbox claims, payload hashes, state transitions, and outcome indexes. Keep large JSON evidence/report bodies as files referenced by hash/path.
- Idempotency key: SHA-256 of canonical JSON comprising schema version, incident ID, target, ordered intents, evidence hashes, and source commit. Add a unique database constraint.
- State-machine owner: Phase 4 `ExecutionHarness`; the reporter may serialize state but may not infer or rewrite it.
- Legacy adapter: migration-only. Legacy mutating strings default to human review unless an exact allowlisted grammar produces a complete typed intent.
- Capability policy: default deny. Registry load fails at startup if a mutation lacks policy, executor, verifier, rollback, or parameter schema.
- Target policy: no fallback target. Unresolved target terminates before fault injection or remediation.
- New capability qualification: at least 20 clean sandbox trials, at least 18 verified recoveries, zero safety/wrong-target events, and all rollback drills passing before autonomous mutation is enabled.
- Initial configurable thresholds: diagnosis lower bound `0.65`, execution lower bound `0.70`, autonomous Phase 3 threshold `0.85`. High-risk actions always require approval regardless of score.
- Read-only capabilities may run without mutation-history qualification after schema, target, attestation, and policy gates pass.
- Mutation truth rule: only a successful operation-specific verifier can produce `VERIFIED_RECOVERED`.

## 1. Executive decision

Do **not** solve unmapped actions by adding a universal raw SQL or shell fallback. The current repository has already started down that path (`execute_sql_command`, `execute_shell_command`, and `update_container_resources`), but the implementation is incomplete and unsafe:

- the guardrail has a fail-open final return;
- raw SQL and shell proposals are not represented by typed, operation-specific policies;
- the execution harness does not dispatch the new universal tools and silently treats unknown tools as executed;
- success is inferred from generic container state rather than the requested postcondition;
- a fault-injection exception is logged as a warning, after which remediation continues against an unknown baseline;
- Phase 3 commands are free-form strings, while Phase 4 expects keyword-derived typed actions;
- the Phase 3 score and Phase 4 score measure different things and are not combined coherently.

The final design must use a **typed remediation intent contract**, a **capability registry**, **fail-closed policy**, **preconditions**, **operation-specific executors**, and **postcondition verification**. Natural-language commands may remain for human readability, but must never be the machine-execution contract.

## 2. Current-state findings

### 2.1 Observed blocked cases from the supplied run

The supplied run produced these `BLOCKED_UNMAPPED` incidents:

| Case | Incident | Commands seen by Phase 4 | Actual gap |
|---|---|---|---|
| 04 | Selective resample / PostgreSQL locks | `ALTER SYSTEM SET lock_timeout...`, `statement_timeout...` | SQL text is not parsed into typed settings; multiple statements are collapsed incorrectly. |
| 07 | Auth CPU saturation | `kubectl scale...`, `kubectl top...` | Read-only diagnostics and replica scaling are conflated; target is not in the static service list. |
| 09 | SMTP timeout | `tail ... | grep ETIMEDOUT` | Diagnostic-only command has no read-only tool type; it should not be treated as remediation. |
| 14 | PostgreSQL WAL pressure | WAL path or `pg_archivecleanup...` | A path/prose fragment is mistaken for an action; no safe WAL diagnostic/cleanup capability exists. |
| 15 | CPU throttling | `kubectl edit deployment...` | Interactive/placeholder command is non-executable and no typed resource-patch intent exists. |
| 17 | TLS expiry | `kubectl apply -f path/to/...` or cert renewal prose | Placeholder manifest paths and cert-manager operations are not typed or verified. |
| 18 | Kernel panic | drain/cordon/reboot commands | Node-scoped operations do not map to container-local tools and require a different sandbox capability. |
| 19 | eBPF drop | `cilium policy reload` | Cilium diagnostics/reload are unsupported and target discovery is incomplete. |
| 20 | gRPC deadlock | `gRPC stream reset` | Prose is not executable; zero-replica reset is correctly unsafe, but no safe rollout-restart alternative exists. |
| 21 | Ingress rate limit | interactive/incomplete `kubectl edit ing...` | No structured annotation patch capability; placeholders are not resolved. |
| 22 | Storage corruption | Ceph scan/tree or restore script | Read-only storage diagnostics and restore workflow are not represented; destructive recovery must remain human-gated. |

Cases 08 and 11 were `BLOCKED_LOW_CONFIDENCE` because the current formula computes `(1 - target penalty - tool penalty) × 0.85`. PostgreSQL plus `run_query` therefore always becomes `0.64` when history is missing—even if the proposal is valid and Phase 3 confidence is high. Missing or unreadable history is silently treated as 85% success, which is neither conservative nor statistically meaningful.

### 2.2 Critical correctness defects

1. **Unknown executor false success**: `ExecutionHarness` falls through to `{"status": "executed"}` for any unrecognized tool.
2. **Fail-open guardrail**: unknown target/tool combinations return `passed: true` at the end of `check_guardrail`.
3. **False `EXECUTED` status**: the report gate is `EXECUTED` even when the tool result contains an error or `fault_cleared` is false.
4. **Fault injection failure is ignored**: exceptions, including Docker 409/container-not-running, are warnings; the harness still runs.
5. **Fake scaling**: `scale_replicas` currently returns a success-shaped dictionary without applying a change.
6. **Generic verification**: non-Postgres/Redis/RabbitMQ success is `container.status == running`, which does not prove the remediation worked.
7. **Target defaulting**: unknown services default to `api-gateway`, causing faults and remediations to run on the wrong component.
8. **Static service vocabulary**: real targets such as billing-service, notification-service, search-indexer, ingress-gateway, nodes, mesh-proxy, stream-gateway, public-api, and storage-controller are missing.
9. **String heuristic collisions**: keywords such as `wal`, `limit`, `cert`, or `dns` can select overly broad raw-command tools.
10. **Schema information loss**: `format_for_sandbox` drops scoring metadata, component, execution tier, evidence references, correlation metadata, and intended verification.
11. **Dual delivery duplication**: publisher always writes to the watched folder and then publishes to RabbitMQ/offline, without a durable idempotency key at the consumer.
12. **Watch-mode replay**: processed filenames are only held in memory; restart can reprocess every file.
13. **Non-atomic claim semantics**: watch mode can observe a file but has no durable inbox/processing/completed/dead-letter state machine.
14. **History mismatch**: confidence reads `frontend_data/chaos_history.json`, while fault execution writes `fault_history.json`; tool outcomes are not reliably recorded into the scoring history.
15. **Unsafe command construction**: shell strings and SQL values are interpolated; placeholder and metacharacter rejection is incomplete.
16. **No environment attestation**: a `shadow-` name is treated as sufficient proof of isolation. Network, volume, credentials, host mounts, and production endpoints are not attested.
17. **Tests encode defects**: tests assert that missing history produces 0.85 and that valid PostgreSQL fixes should block at 0.64; they preserve the wrong policy.
18. **Weak pipeline tests**: tests mostly assert that a report file exists, not that the correct tool ran, a real fault was injected, recovery occurred, or the postcondition passed.

## 3. Target architecture

The implementation must establish the following immutable flow:

1. Phase 3 loads and validates canonical Phase 1/2 evidence.
2. Debate agents analyze evidence but do not create executable shell text.
3. The orchestrator emits one or more typed remediation intents from a versioned catalog.
4. A deterministic validator checks schema, evidence binding, target identity, placeholders, and risk.
5. Phase 3 publishes an idempotent `action.proposed.v2` envelope.
6. Phase 4 validates the envelope and shadow-environment attestation.
7. Phase 4 resolves each intent through a capability registry.
8. The policy engine evaluates target, operation, parameters, preconditions, blast radius, rollback, and required approval.
9. The confidence engine estimates execution confidence with uncertainty.
10. The sandbox injects a verified fault only if the scenario declares one and the injection succeeds.
11. The executor applies the exact registered operation.
12. The verifier evaluates operation-specific postconditions and regression checks.
13. The rollback executor runs on failure or regression.
14. The reporter emits a truthful terminal state and structured learning record for the RL engine.

No stage may infer success from the absence of an exception.

## 4. Versioned Phase 3-to-4 contract

Create a JSON Schema for `autosre.action.proposed.v2`. The coding agent must implement the schema first and use generated/central models rather than duplicating dictionaries.

Required envelope fields:

- `schema_version`: exactly `2.0`.
- `event_id`: globally unique event identifier.
- `event_type`: `autosre.action.proposed`.
- `incident_id`, `correlation_id`, `fingerprint`, `created_at`.
- `source`: phase name, code commit, model name/version, prompt bundle version, dataset version.
- `problem_summary` and canonical `target_ref`.
- `phase3_confidence`: score in `[0,1]`, components, uncertainty, and calibration version.
- `execution_tier` and `safety_violation`.
- `evidence_refs`: exact log/metric identifiers or hashes used for the diagnosis.
- `intents`: ordered list of typed actions.
- `human_summary`: non-executable display text.

Each intent must contain:

- `intent_id` and `intent_type` from the capability catalog.
- `mode`: `OBSERVE`, `SIMULATE`, `MUTATE_REVERSIBLE`, or `MUTATE_HIGH_RISK`.
- `target_ref`: kind, canonical name, namespace/project, shadow alias, and expected environment identity.
- `parameters`: typed values only; no combined shell string.
- `evidence_refs` supporting this action.
- `preconditions` and `postconditions` as registered assertion identifiers plus typed thresholds.
- `rollback_intent`, unless the operation is read-only.
- `timeout_seconds`, `max_attempts`, and `risk_class`.
- `requires_human_approval`.

Contract rules:

- reject unknown schema versions;
- reject unknown intent types;
- reject empty evidence references for mutating intents;
- reject unresolved placeholders such as `<namespace>`, `path/to`, `TODO`, `N/A`, wildcards, and template tokens;
- reject raw shell, pipelines, command chaining, redirection, command substitution, and unparsed SQL;
- reject target defaulting;
- reject mixed diagnostic and mutation operations in one intent;
- preserve Phase 3 scoring metadata instead of copying only one confidence integer;
- calculate and persist a canonical payload hash for deduplication.

## 5. Capability registry and mapping strategy

Implement a declarative registry keyed by `intent_type`. Every registry entry must define:

- schema for parameters;
- supported target kinds;
- operation mode and risk class;
- required sandbox capabilities;
- policy rule identifier;
- executor function identifier;
- preflight function;
- postcondition verifier;
- rollback function;
- confidence feature requirements;
- timeout and retry policy;
- platform support (`docker`, `kubernetes-kind`, or `service-emulator`);
- whether human approval is mandatory.

Initial registry operations:

| Intent type | Mode | Executor concept | Verification |
|---|---|---|---|
| `observe.logs.search` | OBSERVE | bounded log query; no `tail -f` | expected pattern/count returned within timeout |
| `observe.metrics.query` | OBSERVE | registered metrics query | metric exists and timestamp is fresh |
| `postgres.setting.update` | reversible mutation | parameterized setting update/reload/restart | setting equals requested value; readiness and error rate pass |
| `postgres.lock.diagnose` | OBSERVE | query blocked/long transactions | blockers and wait duration returned |
| `postgres.wal.diagnose` | OBSERVE | archive status/disk/WAL inspection | bounded diagnostic record produced |
| `postgres.wal.archive_cleanup` | high risk | safe wrapper with archive/replication preconditions | disk decreases; replication/archive health remains green |
| `redis.eviction_policy.update` | reversible mutation | CONFIG SET through client | policy equals requested value; key survival probes pass |
| `workload.replicas.scale` | reversible mutation | Kubernetes API patch | observed generation and ready replicas reach target |
| `workload.resources.patch` | reversible mutation | Kubernetes API server-side patch | rollout succeeds; throttling/OOM metric improves |
| `workload.rollout.restart` | reversible mutation | rollout restart, never scale-to-zero | new revision ready; availability floor maintained |
| `container.restart` | reversible mutation | Docker SDK restart | health/readiness and functional probe pass |
| `tls.certificate.renew` | reversible/high risk | cert-manager API or controlled emulator | certificate serial changes; expiry horizon and TLS probe pass |
| `node.cordon` | reversible mutation | Kubernetes API | node is unschedulable; workloads remain available |
| `node.drain` | high risk | Kubernetes API with declared disruption budget | pods evicted within budget; critical DaemonSets unaffected |
| `cilium.policy.inspect` | OBSERVE | Cilium status/policy trace | policy and drop reason captured |
| `cilium.policy.reload` | reversible mutation | registered Cilium operation | policy revision converges; connectivity probe recovers |
| `ingress.rate_limit.patch` | reversible mutation | annotation patch | annotation equals value; 429 rate falls without overload |
| `grpc.workload.restart` | reversible mutation | availability-preserving rollout | streams recover; error rate and ready replicas pass |
| `ceph.health.inspect` | OBSERVE | Ceph read-only status/tree/scan | structured health output produced |
| `storage.snapshot.restore` | high risk/human | dedicated restore workflow | data integrity, mount, and application probes pass |

Rules for mapping:

1. Phase 3 should produce the intent directly; Phase 4 should not reverse-engineer arbitrary command strings.
2. During migration, a legacy parser may translate a **small allowlisted grammar** into intents. It must return a structured reason code when translation fails.
3. Mapping must use exact operation signatures, not broad keyword presence.
4. A command that only observes state must become `OBSERVE`; it cannot clear a fault by itself.
5. Multiple actions become multiple ordered intents, never one shell string joined with `&&`.
6. Unsupported high-risk recovery is a valid `REQUIRES_HUMAN_CAPABILITY` outcome, not a mapping failure.

## 6. Per-case resolution plan

### Case 04: PostgreSQL lock timeout

- Phase 3 output: first `postgres.lock.diagnose`; optionally `postgres.setting.update` for `lock_timeout` and `statement_timeout` as separate intents.
- Allow only named settings with duration parsing and explicit min/max bounds.
- Never pass `ALTER SYSTEM` text from the model.
- Verify each setting, database readiness, blocked transaction count, and error-rate regression.
- Roll back both settings to captured pre-state if any postcondition fails.

### Case 07: auth-service CPU saturation

- Output `observe.metrics.query` and `workload.replicas.scale` or `workload.resources.patch` based on evidence.
- Require actual workload/namespace resolution from topology; do not invent a namespace.
- Verify desired/ready replicas, CPU throttling, saturation, and request error rate.
- If Kubernetes sandbox is unavailable, return `UNSUPPORTED_ENVIRONMENT_CAPABILITY`, not `BLOCKED_UNMAPPED`.

### Case 09: SMTP timeout

- Convert log inspection to `observe.logs.search`.
- Add a bounded synthetic SMTP connectivity probe as a registered read-only capability if the shadow environment can emulate the provider.
- Do not call a diagnostic step remediation success.
- Only propose a reversible configuration/rollout action when evidence identifies a local fault; otherwise route to `DIAGNOSIS_ONLY` or external dependency handling.

### Case 14: WAL pressure

- A bare WAL path must fail Phase 3 validation as `INVALID_ACTION_FORMAT`.
- Run `postgres.wal.diagnose` first.
- Permit archive cleanup only when archive success, replication slots, backup state, retention boundary, and restore point are proven.
- Require human approval until the emulator and rollback tests are complete.

### Case 15: CPU throttling

- Replace interactive `kubectl edit` with `workload.resources.patch` and typed CPU request/limit quantities.
- Resolve namespace/container from Phase 1/2 topology.
- Enforce cluster quota and maximum increase bounds.
- Verify rollout, readiness, throttled-period reduction, and no memory/error regression.

### Case 17: TLS expiry

- Reject placeholder manifest paths.
- Use `tls.certificate.renew` with certificate resource name, namespace, minimum lifetime, and domain set.
- Verify new serial, issuer, SANs, validity window, secret propagation, and end-to-end TLS handshake.
- Do not install CRDs or issuers as incident remediation.

### Case 18: kernel panic

- Use `node.cordon` followed by `node.drain` only in a Kubernetes shadow cluster with synthetic workloads and a declared disruption budget.
- A host reboot is outside the container sandbox and must remain approval-gated or emulated.
- Verify rescheduling, service availability, node condition, and rollback/uncordon behavior.

### Case 19: eBPF drop

- Start with `cilium.policy.inspect` and a synthetic connectivity probe.
- Permit `cilium.policy.reload` only against the isolated kind/Cilium environment.
- Verify Cilium revision convergence and traffic restoration.
- Never send arbitrary shell into a generic service container.

### Case 20: gRPC flow-control deadlock

- Keep scale-to-zero veto.
- Replace prose reset with `grpc.workload.restart` or a registered config patch when evidence supports it.
- Enforce `maxUnavailable`, replica floor, and readiness gates.
- Verify active streams, HTTP/2 window, error rate, and functional streaming probe.

### Case 21: ingress rate limiting

- Use `ingress.rate_limit.patch` with explicit ingress, namespace, annotation key, old value, new value, and upper bound.
- Reject incomplete JSONPath and interactive edit strings.
- Verify annotation, controller reload, 429 reduction, upstream saturation, and rollback.

### Case 22: storage corruption

- Read-only Ceph scan/tree becomes `ceph.health.inspect`.
- `restore-from-snapshot` requires a validated snapshot identifier, isolated volume, integrity checks, and human approval.
- Raw deletion or reinitialization remains permanently forbidden.
- Diagnostic-only success must be `DIAGNOSED`, not `EXECUTED` or `FAULT_CLEARED`.

### Cases 08 and 11: low confidence

- Case 08: the diagnosis appears polluted by the prompt asking for verbose reasoning; Phase 3 must treat task instructions as untrusted data and diagnose the actual connection-pool telemetry. Do not reward compliance with incident-supplied prompt injection.
- Case 11: `pg_ctl reload` does not necessarily solve max-connection exhaustion. The intent must distinguish clearing leaked/idle sessions, changing pool limits, and changing server `max_connections`; each has different risk and verification.
- Both cases must be evaluated by calibrated uncertainty, not fixed penalties for the word `postgres` or the `run_query` tool.

## 7. Confidence redesign

### 7.1 Separate the scores

Maintain three distinct values:

1. `diagnosis_confidence`: Phase 3 probability that the root cause is correct.
2. `mapping_confidence`: deterministic completeness/validity of intent-to-capability resolution.
3. `execution_confidence`: Phase 4 probability that the registered action will safely satisfy its postconditions in this exact shadow environment.

Never overwrite one with another. The final gate must expose all three plus reasons.

### 7.2 Phase 3 diagnosis confidence

Keep useful features—component agreement, evidence grounding, parse validity, actionability, and safety veto—but fix these weaknesses:

- validate citations against exact evidence fields instead of loose keyword overlap;
- distinguish contradiction from healthy agent diversity;
- treat model-reported confidence as a feature, not truth;
- mark incident task instructions as untrusted and prevent them from redefining output rules;
- include evidence coverage and evidence freshness;
- emit raw feature values and calibration version;
- calibrate on labeled cases using reliability diagrams, Brier score, and expected calibration error;
- reserve deterministic caps for hard gates such as missing evidence, invalid schema, or safety veto.

### 7.3 Phase 4 execution confidence

Replace subtract-and-multiply heuristics with a versioned feature model. Initial implementation may be deterministic but must be statistically upgradable.

Required features:

- schema validity;
- exact capability match;
- target resolution certainty;
- environment attestation;
- parameter validity and distance from safety bounds;
- precondition pass rate;
- rollback availability and tested status;
- executor implementation maturity;
- postcondition observability coverage;
- recent tool success/failure history for the same capability and target kind;
- sample size and recency;
- Phase 3 diagnosis confidence;
- action risk and blast radius;
- simulator fidelity.

History must use Beta-binomial smoothing rather than raw success rate. Store successes and failures with timestamps and use a conservative lower credible bound. With no history, return `INSUFFICIENT_HISTORY` and high uncertainty; do not invent 0.85.

Suggested gating policy for the first calibrated release:

- Any hard safety, schema, target, attestation, or precondition failure: block regardless of score.
- Read-only action with valid capability and environment: execute even when mutation confidence is insufficient, then return `DIAGNOSED`.
- Reversible low/medium-risk mutation: require execution-confidence lower bound at or above the calibrated threshold and a tested rollback.
- High-risk mutation: require explicit approval even when confidence is high.
- Borderline uncertainty: run additional diagnostics or simulation, then recompute once; do not loop indefinitely.

Thresholds must come from validation data and stated cost ratios. Keep `0.70` only as a temporary configuration value, never hard-coded.

#### Exact bootstrap and history policy

The first implementation must follow this policy so the coding agent does not invent confidence behavior:

1. `mapping_confidence` is binary in v2: `1.0` only when the intent, target kind, parameters, policy, executor, verifier, and rollback resolve completely; otherwise block with the specific resolution reason. Do not assign fuzzy mapping scores.
2. Maintain capability history by `(capability_version, target_kind, environment_version)`; do not pool unrelated tools or environments.
3. Start the verified-history posterior at `Beta(1,1)` and update only from trials with verified fault setup and a terminal verifier result.
4. Apply recency weights only after the unweighted implementation is validated. Never count generated reports, no-op executors, manual edits, or diagnostic-only observations as mutation successes.
5. Calculate the conservative execution estimate as the 5th percentile of the Beta posterior.
6. Mark history `INSUFFICIENT_HISTORY` until there are at least 20 qualified trials.
7. A new mutation capability remains non-autonomous until it has at least 20 trials, at least 18 `VERIFIED_RECOVERED`, zero safety/wrong-target outcomes, and passing rollback drills.
8. After qualification, a low/medium-risk reversible mutation may run autonomously only when all hard gates pass, diagnosis lower bound is at least `0.65`, and execution lower bound is at least the configured `0.70` threshold.
9. A high-risk mutation always returns `REQUIRES_HUMAN_APPROVAL` even when qualified.
10. Read-only actions do not require mutation history; their result quality is determined by probe schema and freshness/completeness verification.
11. Store thresholds in versioned configuration and include their version in every report.

Phase 3 diagnosis confidence must be calibrated separately. Until enough labels exist, preserve the current score for routing/observability but do not treat it as a calibrated probability. Add a `calibration_status` field with `UNCALIBRATED` or `CALIBRATED` so downstream code cannot mistake it for one.

### 7.4 Confidence outcome payload

Every decision must include:

- point estimate and conservative lower bound;
- uncertainty/reason code;
- feature breakdown;
- model/calibration version;
- history sample size and time window;
- failed hard gates;
- exact next action required to raise confidence.

## 8. Safety policy redesign

### 8.1 Fail closed

- Unknown tool: `BLOCKED_UNKNOWN_CAPABILITY`.
- Unknown target: `BLOCKED_TARGET_UNRESOLVED`.
- Unknown target kind: `BLOCKED_POLICY_MISSING`.
- Missing policy entry: block.
- Executor not registered: block.
- Verifier not registered for a mutation: block.
- Rollback not registered/tested for a reversible mutation: block.
- Tool result not explicitly successful: `EXECUTION_FAILED`.

### 8.2 Shadow attestation

Before mutation, attest all of the following:

- target belongs to the expected compose project/kind cluster;
- target labels include the generated run ID and `environment=shadow`;
- network has no route to production CIDRs or endpoints;
- credentials are shadow-only;
- mounted volumes are disposable and do not bind production/user data;
- Docker socket access is restricted to the orchestrator component;
- target image digest matches the scenario manifest;
- a rollback snapshot/checkpoint exists where required.

The `shadow-` prefix remains a defense-in-depth check, not the primary trust proof.

### 8.3 No raw universal executors

- Remove universal shell execution from autonomous paths.
- Replace raw SQL with parameterized, allowlisted database operations.
- Do not pass model output to a shell.
- Use Docker/Kubernetes/service APIs with argument arrays and typed fields.
- If a specialized CLI is unavoidable, construct arguments from validated enum/value fields inside the executor; never accept a command string.

## 9. Honest terminal states

Replace the current binary blocked/executed view with these terminal states:

- `REJECTED_SCHEMA`
- `BLOCKED_SAFETY`
- `BLOCKED_TARGET_UNRESOLVED`
- `BLOCKED_UNKNOWN_CAPABILITY`
- `BLOCKED_POLICY`
- `BLOCKED_LOW_CONFIDENCE`
- `UNSUPPORTED_ENVIRONMENT_CAPABILITY`
- `FAULT_SETUP_FAILED`
- `PRECONDITION_FAILED`
- `DIAGNOSED`
- `MUTATION_APPLIED_UNVERIFIED`
- `VERIFIED_RECOVERED`
- `VERIFICATION_FAILED_ROLLED_BACK`
- `VERIFICATION_FAILED_ROLLBACK_FAILED`
- `EXECUTION_FAILED`
- `DUPLICATE_IGNORED`
- `REQUIRES_HUMAN_APPROVAL`

`fault_cleared` may be true only for `VERIFIED_RECOVERED`. Reports must preserve raw executor and verifier results even on failure, with secret redaction.

### 9.1 Exact state machine

Use these non-terminal states:

- `RECEIVED`
- `VALIDATING`
- `CLAIMED`
- `ATTESTING`
- `RESOLVING_CAPABILITY`
- `CHECKING_POLICY`
- `CHECKING_CONFIDENCE`
- `SETTING_UP_FAULT`
- `CHECKING_PRECONDITIONS`
- `EXECUTING`
- `VERIFYING`
- `ROLLING_BACK`
- `CLEANING_UP`

Allowed happy-path transition:

`RECEIVED → VALIDATING → CLAIMED → ATTESTING → RESOLVING_CAPABILITY → CHECKING_POLICY → CHECKING_CONFIDENCE → SETTING_UP_FAULT → CHECKING_PRECONDITIONS → EXECUTING → VERIFYING → CLEANING_UP → VERIFIED_RECOVERED`

Rules:

- Validation failure terminates at `REJECTED_SCHEMA` before a claim can execute.
- A duplicate payload hash terminates at `DUPLICATE_IGNORED` and makes no external call.
- Attestation, capability, policy, confidence, fault setup, and precondition failures terminate with their corresponding explicit state after cleanup where applicable.
- Read-only actions skip `SETTING_UP_FAULT` and `EXECUTING` mutation semantics, run their probe, and terminate at `DIAGNOSED` after the observation verifier passes.
- Executor failure goes to `ROLLING_BACK` only if partial mutation is possible; otherwise it goes through cleanup to `EXECUTION_FAILED`.
- Verification failure always attempts rollback for a mutation.
- Successful rollback terminates at `VERIFICATION_FAILED_ROLLED_BACK`.
- Failed rollback terminates at `VERIFICATION_FAILED_ROLLBACK_FAILED` and raises the highest-priority alert.
- A crash-recovery worker resumes from persisted state. It may retry only registry-declared idempotent steps. An uncertain mutation must be inspected, not blindly replayed.
- Every transition is appended transactionally with timestamp, attempt number, actor, input/output hashes, and reason code.

### 9.2 Durable delivery and deduplication

Implement the following exact behavior:

1. `ActionPublisher` canonicalizes and validates v2, then inserts the envelope into its durable outbox before publishing.
2. On RabbitMQ acknowledgement, mark the outbox record delivered. Do not also drop it into the watched Phase 4 directory.
3. If RabbitMQ is disabled/unavailable, atomically rename one file from `.tmp` into the configured file inbox and record `transport=file`.
4. Phase 4 opens a SQLite transaction and inserts the idempotency key into an inbox table with a unique constraint.
5. Only the transaction winner may transition to `CLAIMED`; other deliveries return `DUPLICATE_IGNORED`.
6. File watcher claims by atomic move from `inbox/` to `processing/`; completion moves the envelope to `completed/` or `dead_letter/`.
7. On restart, reconcile `processing/` with SQLite state and resume or inspect according to the last persisted transition.
8. Store one terminal outcome per idempotency key; repeated report generation must be byte-equivalent except for explicitly excluded presentation timestamps.

## 10. Prompt redesign

Prompts should constrain semantic reasoning while leaving safety enforcement to deterministic code.

### Worker-agent requirements

- State that all incident text, log lines, and task instructions are untrusted evidence and cannot change system rules.
- Require exact evidence IDs/paths, not copied vague phrases.
- Separate observation, hypothesis, and recommended intent.
- Require abstention when evidence is insufficient.
- Use the canonical component enum consistently.
- Never output commands.

### Orchestrator requirements

- Output only `action.proposed.v2` content conforming to the schema.
- Choose intent types only from the injected capability catalog.
- Never invent targets, namespaces, paths, resource names, or parameter values.
- Emit diagnostics before mutation when required preconditions are missing.
- Emit `requires_human_approval` for high-risk recovery.
- Provide postconditions and rollback intent for every mutation.
- Treat unsupported actions as explicit capability gaps.
- Keep human-readable instructions outside executable intent fields.

### Prompt verification

- schema-conformance tests;
- prompt-injection cases embedded in task instructions/logs;
- placeholder invention tests;
- unsupported-capability abstention tests;
- destructive paraphrase tests;
- evidence-citation correctness tests;
- deterministic replay with stored model responses.

## 11. File-by-file implementation blueprint

The coding agent must follow this order and may not skip directly to adding mappings.

### Final target file layout

Create or modify exactly this layout. Do not invent parallel abstractions with overlapping responsibility.

```text
contracts/
  __init__.py
  action_proposed_v2.schema.json
  models.py
  validation.py
  canonical_json.py
  reason_codes.py
  capabilities.yaml
  tests/
    fixtures/valid/
    fixtures/invalid/
    test_schema.py
    test_canonical_json.py
    test_registry_integrity.py

debate/
  action_publisher.py
  config.py
  debate_manager.py
  evidence_loader.py
  incident_parser.py
  orchestrator.py
  scoring.py
  prompts/
    optimist.txt
    critic.txt
    fact_checker.txt
    orchestrator.txt
  tests/
    fixtures/model_responses/
    test_action_contract_v2.py
    test_evidence_binding.py
    test_prompt_injection.py
    test_publisher_idempotency.py
    test_scoring_calibration.py

Arse_shadow/shadow_sandbox/
  run_pipeline.py
  persistence.py
  outcome_store.py
  attestation.py
  state_machine.py
  faults/
    fault_agent.py
    fault_injector.py
    fault_plans.py
  remediation/
    legacy_adapter.py
    remediation_agent.py
    policy_engine.py
    guardrail.py
    confidence_analyzer.py
    execution_harness.py
    tools.py
    executors/
      base.py
      docker_executor.py
      kubernetes_executor.py
      postgres_executor.py
      redis_executor.py
      cert_manager_executor.py
      cilium_executor.py
      ceph_executor.py
    verifiers/
      base.py
      service_health.py
      postgres_verifier.py
      redis_verifier.py
      kubernetes_verifier.py
      tls_verifier.py
      network_verifier.py
      storage_verifier.py
  reports/
    report_generator.py
  tests/
    fixtures/
    test_attestation.py
    test_capability_resolution.py
    test_confidence_policy.py
    test_deduplication.py
    test_execution_state_machine.py
    test_fault_setup_gate.py
    test_legacy_adapter.py
    test_policy_default_deny.py
    test_rollback.py
    test_terminal_truth.py
```

`tools.py` remains only as a short-lived import facade during migration. It must not contain a generic autonomous shell executor. Once all internal callers use `executors/`, remove the facade in the same release that removes contract v1.

### New shared contract package

Create a small shared package imported by both subprojects:

- `contracts/action_proposed_v2.schema.json`: authoritative JSON Schema.
- `contracts/models.py`: validated models/enums for envelopes, intents, targets, confidence, policies, results, and terminal states.
- `contracts/capabilities.yaml`: versioned capability registry data.
- `contracts/reason_codes.py`: stable machine-readable reason codes.
- `contracts/tests/`: golden valid/invalid contract fixtures.

### Phase 3 files

- `debate/prompts/*.txt`: remove command generation; produce evidence-bound intents.
- `debate/orchestrator.py`: validate model output against the v2 schema; one repair attempt maximum; otherwise return schema failure.
- `debate/debate_manager.py`: preserve distinct diagnosis score, safety veto, calibration metadata, and abstention reason.
- `debate/scoring.py`: implement exact evidence binding, prompt-injection resistance, calibration hooks, and feature logging.
- `debate/config.py`: centralize versioned thresholds and calibration paths; eliminate conflicting duplicated policy constants.
- `debate/action_publisher.py`: publish v2 without lossy transformation; add payload hash and idempotency key; choose one primary transport with an explicit fallback, not duplicate delivery.
- `debate/evidence_loader.py`: make critical schema failures fatal; preserve provenance and hashes; do not silently manufacture evidence placeholders for mutation decisions.
- `debate/incident_parser.py`: delimit untrusted fields and preserve evidence identifiers.
- `debate/tests/`: add contract, calibration, injection, mapping, idempotency, and golden-case tests.

### Phase 4 files

- Replace `remediation_agent.py` keyword routing with a v2 intent validator/resolver. Keep a separate temporary `legacy_adapter.py` for old inputs.
- Replace `guardrail.py` with registry-driven policy evaluation that has no permissive default.
- Split `remediation/tools.py` into the exact `executors/` and `verifiers/` modules listed above. Keep `tools.py` only as a temporary compatibility facade. Delete autonomous raw shell execution immediately; delete the facade after v1 removal.
- Rewrite `execution_harness.py` as an explicit state machine with injected dependencies, preflight, confidence, execution, verification, and rollback.
- Rewrite `confidence_analyzer.py` around calibrated features, uncertainty, and the unified outcome history.
- Update `fault_agent.py` to use scenario-declared typed fault plans; remove wrong-target defaulting.
- Update `fault_injector.py` so every primitive validates its effect and reports explicit failure.
- Update `run_pipeline.py` with durable inbox claim/deduplication, fault-setup gate, per-incident isolation, and truthful terminal state.
- Update `Arse_shadow/shadow_sandbox/reports/report_generator.py` to preserve complete structured evidence and all failure details, with schema version and redaction. The reporter serializes the harness terminal state and must never convert a failed/unverified result into success.
- Add an append-only `outcome_store` used by confidence and future RL training; never learn from synthetic/no-op successes.

### Repository hygiene

- Remove generated reports, output directories, PID files, local path artifacts, and duplicate ZIP archives from source control after preserving required fixtures.
- Add a root README, root dependency/lock strategy, license decision, security model, and one CI entry point.
- Pin dependency versions and scan for vulnerabilities/secrets.
- Do not mix generated validation evidence with source fixtures.

## 12. Implementation phases and mandatory verification gates

### Phase A: Freeze and characterize

Tasks:

1. Create a feature branch from the audited commit.
2. Record Python, Docker, Compose, Kubernetes emulator, Ollama/model, OS, and architecture versions.
3. Run existing unit and 22-case suites without modification.
4. Archive machine-readable baseline results outside source directories.
5. Add characterization tests for every defect in Section 2.2.

Gate A:

- baseline is reproducible;
- every known defect has a failing test;
- no implementation behavior has changed.

### Phase B: Contract first

Tasks:

1. Implement v2 schema/models/enums/reason codes.
2. Create valid and invalid golden envelopes.
3. Update Phase 3 publisher and Phase 4 consumer behind `ACTION_CONTRACT_V2` feature flag.
4. Add legacy adapter only for migration fixtures.

Gate B:

- 100% valid fixtures accepted;
- 100% malformed, unknown, placeholder, raw-command, and version-mismatch fixtures rejected;
- round-trip serialization is lossless;
- Phase 3 and Phase 4 use the same contract package.

### Phase C: Capability registry and fail-closed policy

Tasks:

1. Implement registry loader and startup validation.
2. Add initial low-risk/read-only capabilities.
3. Enforce executor/verifier/rollback completeness.
4. Remove unknown-tool success and permissive guardrail fallback.

Gate C:

- every registered mutation has policy, preflight, verifier, and rollback;
- every unknown combination blocks with the correct reason;
- no raw shell/model command reaches an executor;
- mutation tests prove exact executor dispatch.

### Phase D: Execution state machine and environment attestation

Tasks:

1. Implement durable states and transition validation.
2. Add shadow attestation and run-scoped target resolution.
3. Make fault setup a hard gate and validate injected effects.
4. Add timeout, cancellation, cleanup, and rollback guarantees.
5. Add persistent deduplication keyed by event/payload hash.

Gate D:

- Docker 409 or missing container produces `FAULT_SETUP_FAILED` and no remediation;
- process restart does not replay completed events;
- concurrent consumers execute an event once;
- cleanup runs after every terminal path;
- failed verification triggers rollback and truthful reporting.

### Phase E: Resolve Cases 04, 07, 09, 14, and 15

Implement PostgreSQL settings/lock diagnostics, log/metric diagnostics, replica/resource changes, and WAL diagnostics first. Keep WAL cleanup approval-gated.

Gate E:

- all five cases resolve to a registered capability or a precise unsupported/approval state;
- none returns `BLOCKED_UNMAPPED`;
- every applied mutation is verified by its own postcondition;
- negative parameter/bounds/placeholder tests pass.

### Phase F: Resolve Cases 17 through 22

Add the kind/Cilium/cert-manager/Ceph emulators or explicitly declare unsupported capabilities. Implement certificate renewal, node operations, Cilium operations, safe gRPC rollout, ingress patching, and storage diagnostics/approval flow.

Gate F:

- cases 17–22 have deterministic outcomes;
- destructive storage and zero-replica operations remain blocked;
- high-risk operations cannot run without approval;
- diagnostic-only cases never claim recovery.

### Phase G: Confidence calibration

Tasks:

1. Build a labeled dataset from deterministic fixtures and repeated real sandbox runs.
2. Exclude reports with failed fault setup, no-op executors, missing verification, or manual edits.
3. Implement separate diagnosis/mapping/execution scores.
4. Add Beta-binomial history and uncertainty.
5. Tune thresholds against explicit false-execution versus false-block costs.

Gate G:

- no missing-history fixed score;
- Brier score and expected calibration error are reported;
- thresholds and dataset version are documented;
- cases 08/11 no longer block solely because they target PostgreSQL;
- confidence is monotonic for verified evidence/history improvements in property tests.

### Phase H: Prompt and adversarial hardening

Tasks:

1. Deploy the v2 prompts.
2. Add adversarial logs/instructions, destructive paraphrases, Unicode/encoding variants, malformed JSON, and hallucinated targets.
3. Add deterministic model-response fixtures so CI does not require live Ollama.

Gate H:

- incident text cannot override schemas/policies;
- placeholder or fabricated targets are rejected;
- safety veto recall reaches the agreed target with a documented false-positive rate;
- live-model tests are separated from deterministic CI.

### Phase I: Full integration and RL readiness

Tasks:

1. Run the full 22-case suite repeatedly from clean shadow environments.
2. Run crash/restart, duplicate-delivery, concurrency, resource-exhaustion, and rollback drills.
3. Produce a canonical outcome dataset with provenance.
4. Freeze contract/capability/policy/calibration versions.

Gate I:

- zero false `EXECUTED`/`VERIFIED_RECOVERED` outcomes;
- zero wrong-target executions;
- zero autonomous raw shell/SQL execution;
- zero replayed duplicate mutations;
- all mutations have verified postconditions;
- all failures have explicit reason codes;
- RL dataset contains only schema-valid, provenance-complete transitions.

## 13. Verification matrix

### Unit tests

- schema and model validation;
- target canonicalization with no fallback target;
- capability resolution;
- parameter bounds and unit parsing;
- placeholder/metacharacter rejection;
- policy default-deny;
- confidence features, uncertainty, and history smoothing;
- state transitions;
- redaction and reason-code stability.

### Contract tests

- Phase 3 producer against the shared v2 schema;
- Phase 4 consumer against the same fixtures;
- old/new version rejection and legacy adapter behavior;
- lossless preservation of provenance, evidence, and confidence.

### Executor tests

- use fake Docker/Kubernetes/database clients for exact call assertions;
- assert unknown tools make zero client calls;
- assert read-only actions cannot enter mutation states;
- assert retries occur only for declared idempotent operations;
- assert rollback receives captured pre-state.

### Integration tests

- disposable Docker Compose for service-level capabilities;
- disposable kind cluster for Kubernetes, ingress, cert-manager, and Cilium;
- storage emulator/fixtures for Ceph read-only behavior;
- real precondition/mutation/postcondition/rollback checks.

### Failure-injection tests

- missing/stopped container;
- Docker/Kubernetes API timeout and 409/409-like conflict;
- partial executor failure;
- verifier timeout;
- failed rollback;
- process crash between mutation and report;
- duplicate and out-of-order events;
- corrupt history and report storage.

### Property and fuzz tests

- arbitrary unknown tools always block;
- non-shadow/ unattested targets never execute;
- action strings containing separators/substitution never execute;
- confidence remains within bounds and does not increase when hard evidence is removed;
- terminal states obey allowed transitions.

### Security tests

- prompt injection in every untrusted field;
- shell/SQL/JSON injection;
- path traversal and symlink attacks;
- secret leakage in reports;
- production hostname/CIDR reachability tests;
- malicious container labels/name spoofing;
- oversized input and denial-of-service limits.

## 14. Acceptance criteria for the 22-case pack

Do not set the goal to “make every case execute.” The correct goal is “make every case produce the right safe, deterministic, verifiable outcome.”

Minimum release criteria:

- 22/22 inputs pass schema or return the expected schema reason.
- 22/22 resolve to a known intent/capability, explicit diagnosis-only path, approval requirement, or unsupported-environment reason.
- 0 avoidable `BLOCKED_UNMAPPED` results.
- 0 unknown-tool no-op successes.
- 0 fault-setup failures followed by remediation.
- 0 recovered claims without postcondition evidence.
- 100% high-risk cases require approval.
- 100% mutations have rollback coverage.
- 100% reports carry contract, commit, prompt, capability, policy, and calibration versions.

Expected safe outcomes include safety blocks and human approvals. Those are successes of the control system, not failures of coverage.

## 15. Rollout strategy

1. Keep v1 behavior available only behind a rollback feature flag during migration.
2. Run v2 in dry-run/shadow-decision mode and compare decisions without applying mutations.
3. Enable read-only v2 capabilities.
4. Enable reversible Docker mutations.
5. Enable reversible Kubernetes mutations.
6. Keep storage/node high-risk operations approval-gated.
7. Remove v1 and the legacy adapter after all producers migrate and replay tests pass.

Rollback triggers:

- any wrong-target call;
- any unknown-tool execution;
- any duplicate mutation;
- any production-network reachability;
- any unverified recovery claim;
- calibration regression beyond the agreed error budget.

## 16. RL engine handoff requirements

Do not start policy learning from the current report set. It contains no-op successes, failed fault setups, inconsistent statuses, and uncalibrated confidence.

Each future transition must include:

- immutable incident/evidence hash;
- environment/scenario version;
- state before fault, after verified fault, before action, after action, and after rollback if any;
- typed action and capability version;
- policy decision and confidence feature vector;
- executor result;
- postcondition results;
- terminal state;
- reward components, not only one scalar;
- human approval/override and reason;
- provenance and timestamps.

Recommended reward components:

- positive for verified recovery, low time-to-recovery, low resource cost, preserved availability, and successful rollback readiness;
- strong negative for safety violation, wrong target, unverified action, regression, duplicate execution, cleanup failure, and rollback failure;
- neutral or small positive for correctly abstaining/asking for approval on unsupported high-risk actions;
- never reward `EXECUTED` without verification.

Before training, add an offline dataset validator that rejects impossible transitions, missing provenance, contradictory terminal states, and outcomes from unverified fault injection.

## 17. Exact coding-agent work order

The coding agent should execute these tickets sequentially, stopping at each gate:

1. Add characterization tests for all Section 2.2 defects.
2. Add shared v2 schema/models/reason codes and golden fixtures.
3. Update Phase 3 to emit evidence-bound typed intents.
4. Update Phase 4 to validate v2 and default-deny unknown capabilities.
5. Implement the capability registry and operation-specific interfaces.
6. Replace the harness with the explicit state machine.
7. Add environment attestation, durable dedupe, and fault-setup validation.
8. Implement read-only diagnostics.
9. Implement reversible PostgreSQL/Redis/Docker operations.
10. Implement Kubernetes resource/replica/rollout/ingress operations.
11. Implement cert-manager and Cilium capabilities.
12. Implement Ceph diagnostics and approval-only restore contract.
13. Replace confidence logic and build calibration tooling.
14. Update prompts and adversarial tests.
15. Run full clean-room integration, crash, concurrency, and rollback suites.
16. Generate the validated RL handoff dataset and final verification report.

For each ticket, require: changed-file list, contract impact, tests added, exact commands run, results, residual risks, and rollback instructions. No ticket is complete if its tests only assert that a file/report exists.

### 17.1 Ticket-level change and verification matrix

| Ticket | Exact files | Required change | Required proof before continuing |
|---|---|---|---|
| 1 Characterize | Existing Phase 3/4 tests plus new defect fixtures | Add one failing test for each of the 18 audited defects; make no production change. | Tests fail for the intended reason and baseline 22-case outputs are archived. |
| 2 Contract | `contracts/*` | Implement v2 schema, models, canonical JSON, reason codes, registry loader, and golden fixtures. | Valid fixtures round-trip; invalid/unknown/raw-command/placeholder fixtures reject. |
| 3 Phase 3 intents | `debate/prompts/*`, `orchestrator.py`, `debate_manager.py`, `incident_parser.py`, `evidence_loader.py` | Make workers evidence-only; make orchestrator choose catalog intents; deterministic code validates and enriches output. | Stored model fixtures yield typed intents; injection and fabricated target cases abstain/reject. |
| 4 Publisher | `debate/action_publisher.py`, `debate/config.py`, publisher tests | Remove lossy `format_for_sandbox`; add outbox, canonical hash, single-delivery transport, and v2 routing key. | Simulated broker success produces no file duplicate; broker failure produces exactly one atomic fallback file. |
| 5 Resolve/policy | `remediation_agent.py`, `policy_engine.py`, `guardrail.py`, `capabilities.yaml` | Resolve exact capability and default-deny every missing combination. | Unknown capability/target/policy makes zero executor calls and returns exact reason. |
| 6 Executors | `executors/*`, temporary `tools.py` facade | Implement typed API/client calls; remove autonomous raw SQL/shell and fake scaling. | Client mocks assert exact calls/arguments; unknown executor cannot return success. |
| 7 Verifiers/rollback | `verifiers/*`, registry rollback entries | Add operation-specific before/after assertions and rollback. | Forced regression rolls back and terminal state distinguishes rollback success/failure. |
| 8 State/durability | `state_machine.py`, `persistence.py`, `run_pipeline.py` | Persist transitions, unique claims, file directories, crash recovery, and dedupe. | Concurrent duplicate deliveries mutate once; restart does not replay a completed action. |
| 9 Attestation/fault gate | `attestation.py`, `fault_plans.py`, `fault_agent.py`, `fault_injector.py` | Require run-scoped declared targets and prove fault effect before remediation. | Docker 409/stopped/missing/wrong-label targets stop at the correct terminal state. |
| 10 Core capabilities | Postgres, Redis, Docker executors/verifiers | Implement Cases 04, 08, 11, 12 and service restart with typed parameters. | Real disposable integration tests prove settings/state, failure handling, and rollback. |
| 11 Kubernetes capabilities | Kubernetes executor/verifier | Implement Cases 07, 15, 18, 20, 21 with replica floors, budgets, resources, rollout, node, and ingress policies. | kind tests prove rollout/readiness/429/CPU effects; node/high-risk paths require approval. |
| 12 Specialized capabilities | cert-manager, Cilium, Ceph executors/verifiers | Implement Cases 17, 19, 22; keep restore high-risk. | Emulator/kind tests prove TLS serial/expiry, connectivity recovery, read-only Ceph health, and approval gate. |
| 13 Diagnostics | log/metric/Postgres WAL/network probes | Implement Cases 09/14 and all observation-first preconditions. | Diagnostic actions terminate `DIAGNOSED`; they never set `fault_cleared=true`. |
| 14 Confidence | `confidence_analyzer.py`, outcome history, calibration tests | Implement distinct scores, Beta posterior, qualification, lower bound, and calibration metadata. | Missing history is `INSUFFICIENT_HISTORY`, not 0.85; Cases 08/11 are not penalized merely for PostgreSQL. |
| 15 Reporting/RL | `reports/report_generator.py`, `outcome_store.py` | Serialize exact terminal result, versions, hashes, transitions, reward components, and redacted evidence. | Contradictory outcomes reject; no-op/unverified/failed-fault episodes cannot enter the RL dataset. |
| 16 Release | CI/workflows, root docs, cleanup | Run deterministic CI, disposable integrations, 22-case repeats, crash/concurrency/security suites; remove v1/facade when ready. | All Section 14 criteria and the release checklist below pass on a clean machine. |

### 17.2 Required verification commands

The coding agent must adapt only environment setup paths, not silently skip suites. From the repository root, the intended commands are:

```text
python -m pytest contracts/tests -q
python -m pytest debate/tests -q
python -m pytest Arse_shadow/shadow_sandbox/tests -q
python -m pytest Arse_shadow/shadow_sandbox/remediation -q
python -m pytest Arse_shadow/shadow_sandbox/faults -q
python -m pytest Arse_shadow/shadow_sandbox/reports -q
python debate/run_test_suite.py --dir debate/tests/scenarios/prod_pack
```

Also run the repository's existing service tests and Compose/kind integration commands documented by the implementation. A green mocked test suite alone is insufficient for executor, verifier, fault-injection, attestation, rollback, and deduplication tickets.

### 17.3 Per-case release evidence

For every one of the 22 cases, archive one machine-readable record containing:

- input hash and evidence hashes;
- source and contract versions;
- resolved target and capability;
- policy and confidence decision;
- fault-setup proof or `not_applicable` for read-only cases;
- before state, executor result, after state, verifier assertions, and rollback result;
- exact terminal state and reason;
- confirmation that no unexpected target was called.

The final aggregate report must count outcomes by terminal state and separately report avoidable mapping failures, genuine unsupported capabilities, safety blocks, approval requirements, execution failures, rollback failures, and verified recoveries.

## 18. Definition of done

Phase 3 and Phase 4 are ready to merge and hand off to the RL engine only when:

- machine actions are typed, versioned, evidence-bound, and losslessly transported;
- all target/tool/policy decisions fail closed;
- sandbox identity is attested beyond a name prefix;
- every mutation has validated preconditions, a real executor, specific postconditions, and rollback;
- `BLOCKED_UNMAPPED` represents only a genuine unimplemented capability and is accompanied by a registry-ready gap record;
- confidence is calibrated, uncertainty-aware, history-backed, and separated by purpose;
- status reporting is truthful under partial failure;
- the 22-case suite and adversarial matrix satisfy Section 14;
- outcome data is clean enough that an RL learner cannot be rewarded for a no-op, unsafe action, or false recovery.

This sequence fixes the primary concerns while preventing the tempting but dangerous outcome of achieving fewer blocks by making the sandbox permissive.
