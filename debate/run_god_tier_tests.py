import asyncio
import json
import os
import time
from pathlib import Path
from debate_manager import DebateManager
from scoring import warmup_ollama_model_async

async def run_god_tier_test_suite():
    # 1. Warm up Ollama GPU VRAM buffers before starting timer/suite
    await warmup_ollama_model_async()

    manager = DebateManager()
    test_dir = Path("tests/scenarios/god_tier")
    
    if not test_dir.exists():
        print(f"Error: Directory {test_dir} does not exist.")
        return

    scenario_files = sorted([f for f in test_dir.iterdir() if f.suffix == '.json'])
    
    if not scenario_files:
        print(f"No JSON test cases found in {test_dir}")
        return

    print("\n" + "=" * 70)
    print("        GOD TIER MULTI-AGENT RCA ENGINE ADVERSARIAL SUITE        ")
    print("=" * 70)
    print(f"Found {len(scenario_files)} adversarial test scenarios.\n")
    
    results = []

    for file_path in scenario_files:
        print(f"[{time.strftime('%H:%M:%S')}] Running {file_path.name}...")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                incident_payload = json.load(f)
            except json.JSONDecodeError as e:
                print(f"  -> Error parsing JSON: {e}")
                continue
                
        start_time = time.time()
        
        # Run the multi-agent engine asynchronously
        res = await manager.run_async(incident_payload)
        duration = time.time() - start_time
        
        results.append({
            "case": file_path.name,
            "tier": res.get("execution_tier", "N/A"),
            "score": res.get("confidence_score", 0),
            "safety": res.get("safety_violation", False),
            "latency": res.get("total_latency_seconds", round(duration, 2)),
            "round_2": res.get("round_2_executed", False)
        })
        
        # Short cooldown to reset GPU VRAM
        await asyncio.sleep(2)

    report_lines = []
    report_lines.append("# GOD TIER MULTI-AGENT RCA ENGINE VALIDATION REPORT")
    report_lines.append(f"**Execution Timestamp:** `{time.strftime('%Y-%m-%dT%H:%M:%SZ')}`  ")
    report_lines.append(f"**Total Scenarios Evaluated:** `{len(results)}`  \n")
    report_lines.append("---")
    report_lines.append("## 1. Adversarial Test Results Matrix\n")
    report_lines.append("| Case File | Execution Tier | Score (%) | Safety Veto | Latency (s) | Round 2 Re-sampled |")
    report_lines.append("| :--- | :--- | :---: | :---: | :---: | :---: |")

    print("\n" + "=" * 75)
    print("                   GOD TIER VALIDATION REPORT SUMMARY                   ")
    print("=" * 75)
    print(f"{'Case File':<35} | {'Tier':<28} | {'Score':<5} | {'Safety':<6} | {'Latency':<7}")
    print("-" * 75)

    for r in results:
        print(f"{r['case']:<35} | {r['tier']:<28} | {r['score']:>4}% | {str(r['safety']):<6} | {r['latency']:>5.2f}s")
        report_lines.append(f"| `{r['case']}` | `{r['tier']}` | `{r['score']}%` | `{r['safety']}` | `{r['latency']}s` | `{'Yes' if r['round_2'] else 'No'}` |")

    print("=" * 75)

    # Save Validation Report to output directory
    os.makedirs("output", exist_ok=True)
    report_path = Path("output/god_tier_validation_report.md")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nSaved Validation Report to: {report_path.resolve()}")

if __name__ == "__main__":
    asyncio.run(run_god_tier_test_suite())
