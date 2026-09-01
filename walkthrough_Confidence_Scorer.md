# Walkthrough - Confidence Ratio Analyser Implementation

We have implemented the **Confidence Ratio Analyser** in Layer 3 (Remediation) and exposed its metrics in Layer 4 (Reports) of the shadow sandboxing subsystem.

## Changes Made

---

### Layer 3: Remediation Subsystem

#### [confidence_analyzer.py](file:///c:/Users/Shashank/OneDrive/Desktop/Smart%20horizon%20hackathon/Arse_shadow/shadow_sandbox/remediation/confidence_analyzer.py)
- Created module `shadow_sandbox/remediation/confidence_analyzer.py`.
- Implemented `calculate_confidence(proposal, history_path=None)`:
  - **Base score**: Starts at `1.0`.
  - **Target penalty**: Subtracts `0.15` if target contains `"postgres"` or `"redis"`.
  - **Tool penalty**: Subtracts `0.10` if tool is `"restart_container"` or `"run_query"`.
  - **Historical modifier**: Multiplies score by tool success rate read from `history_path` (defaulting to `frontend_data/chaos_history.json`, fallback multiplier `0.85` if file/tool entry is missing).
  - **Output**: Returns score clamped between `0.0` and `1.0`, rounded to 2 decimal places.

#### [execution_harness.py](file:///c:/Users/Shashank/OneDrive/Desktop/Smart%20horizon%20hackathon/Arse_shadow/shadow_sandbox/remediation/execution_harness.py)
- Imported `calculate_confidence` and added `history_path` optional argument to `ExecutionHarness.__init__`.
- Evaluates `conf_score = calculate_confidence(proposal, history_path=self.history_path)` immediately after static guardrails pass.
- If `conf_score < 0.70`:
  - Halts execution pipeline before real tool invocation.
  - Returns outcome record with `gate_decision`: `"BLOCKED_LOW_CONFIDENCE"`, `confidence_score`: `conf_score`, `human_intervention_required`: `True`, and message: `"Execution halted: Confidence ratio [score] is below the 0.70 safety threshold."`.
- If `conf_score >= 0.70`:
  - Appends `confidence_score` to outcome dictionary and proceeds with tool execution.

---

### Layer 4: Reports Subsystem

#### [report_generator.py](file:///c:/Users/Shashank/OneDrive/Desktop/Smart%20horizon%20hackathon/Arse_shadow/shadow_sandbox/reports/report_generator.py)
- Extracts `confidence_score` from outcome record.
- Injects `confidence_score` into the root level of the generated JSON report schema.
- Updated `is_blocked = "BLOCKED" in gate_decision`.
- Guarantees `human_intervention_required` is `True` for any decision containing `"BLOCKED"`.
- Added default message generation for `BLOCKED_LOW_CONFIDENCE`.

---

### Unit & Integration Tests

#### [test_confidence_analyzer.py](file:///c:/Users/Shashank/OneDrive/Desktop/Smart%20horizon%20hackathon/Arse_shadow/shadow_sandbox/remediation/test_confidence_analyzer.py)
- Added comprehensive unit tests covering:
  - Base score calculation & fallback behavior when history file is missing.
  - Target penalties (`postgres`, `redis`).
  - Tool penalties (`restart_container`, `run_query`).
  - Combined penalties & custom history file multiplier evaluation.
  - Execution harness pipeline blocking (`< 0.70` threshold).
  - Report generator schema formatting and root-level `confidence_score` injection.

## Verification Results

### Automated Test Suites
1. **Remediation Unit Tests**: `python -m unittest discover -s shadow_sandbox/remediation -p "test_*.py"`
   - Result: `14 passed in 0.258s (OK)`
2. **Reports Unit Tests**: `python -m unittest discover -s shadow_sandbox/reports -p "test_*.py"`
   - Result: `3 passed in 0.017s (OK)`
3. **Pipeline Test**: `python -m shadow_sandbox.test_pipeline`
   - Result: `2 passed in 7.263s (OK)`
4. **Repository Pytest Suite**: `pytest`
   - Result: `61 passed in 3.19s (OK)`
