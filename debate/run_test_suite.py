import os
import json
import time
import argparse
import asyncio
from pathlib import Path
from debate_manager import DebateManager
from scoring import warmup_ollama_model_async

# --- ADDED: Import the publisher ---
from action_publisher import ActionPublisher, build_action_proposed

async def run_test_suite_async(target_dir: str = "tests/scenarios"):
    # Pre-warm GPU VRAM before timers start
    await warmup_ollama_model_async()

    manager = DebateManager()
    publisher = ActionPublisher() # --- ADDED: Initialize Publisher ---
    
    scenarios_dir = Path(target_dir)
    if not scenarios_dir.exists():
        print(f"Error: Directory {scenarios_dir} does not exist.")
        return

    scenario_files = sorted([f for f in scenarios_dir.rglob('*.json') if f.name != 'manifest.json'])
    
    if not scenario_files:
        print(f"No JSON test cases found in {scenarios_dir}")
        return

    print("=" * 65)
    print(f"   MULTI-AGENT RCA TEST SUITE EXECUTION ({scenarios_dir})   ")
    print("=" * 65)
    print(f"Found {len(scenario_files)} test scenarios.")
    
    results_summary = []

    for file_path in scenario_files:
        print(f"\n[{time.strftime('%H:%M:%S')}] Executing: {file_path.name}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                incident_payload = json.load(f)
            except json.JSONDecodeError as e:
                print(f"  -> Error parsing JSON: {e}")
                continue
                
        start_time = time.time()
        
        # Run the multi-agent engine asynchronously
        result = await manager.run_async(incident_payload)
        
        # --- ADDED: Integration Logic ---
        incident_id = incident_payload.get("incident_id", file_path.stem)
        # Inject the original problem text so the Sandbox has context
        result["problem"] = incident_payload.get("problem", "")
        # Build the official messaging envelope
        message = build_action_proposed(incident_id, result)
        # Fire the publisher (which drops it into sample_inputs)
        publisher.publish(message)
        # --------------------------------

        duration = time.time() - start_time
        print(f"  -> Finished in {duration:.2f} seconds.")
        print(f"  -> Execution Tier: {result['execution_tier']} | Confidence: {result['confidence_score']}%")
        
        results_summary.append({
            "test_case": file_path.name,
            "tier": result['execution_tier'],
            "confidence_score": result['confidence_score'],
            "safety_violation": result['safety_violation'],
            "latency_seconds": result['total_latency_seconds']
        })
        
        # Small sleep between runs to cool down GPU VRAM
        await asyncio.sleep(2)

    print("\n" + "=" * 75)
    print("                FULL 22-CASE TEST SUITE VALIDATION SUMMARY                ")
    print("=" * 75)
    print(f"{'Test Case':<38} | {'Tier':<26} | {'Score':<5} | {'Latency':<6}")
    print("-" * 75)
    
    report_lines = [
        "# FULL 22-CASE RCA ENGINE VALIDATION REPORT",
        f"**Execution Timestamp:** `{time.strftime('%Y-%m-%dT%H:%M:%SZ')}`  ",
        f"**Total Scenarios Evaluated:** `{len(results_summary)}`  \n",
        "---",
        "## Results Matrix\n",
        "| Test Case | Execution Tier | Score (%) | Safety Veto | Latency (s) |",
        "| :--- | :--- | :---: | :---: | :---: |"
    ]

    for res in results_summary:
        print(f"{res['test_case']:<38} | {res['tier']:<26} | {res['confidence_score']:>4}% | {res['latency_seconds']:>5.1f}s")
        report_lines.append(f"| `{res['test_case']}` | `{res['tier']}` | `{res['confidence_score']}%` | `{res['safety_violation']}` | `{res['latency_seconds']}s` |")
    print("=" * 75)

    os.makedirs("output", exist_ok=True)
    report_path = Path("output/full_22_case_validation_report.md")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nSaved Validation Report to: {report_path.resolve()}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Multi-Agent RCA Test Suite")
    parser.add_argument("--dir", type=str, default="tests/scenarios", help="Target scenarios directory")
    args = parser.parse_args()
    
    asyncio.run(run_test_suite_async(args.dir))