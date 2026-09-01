import asyncio
import json
import sys
from pathlib import Path
from debate_manager import DebateManager

# --- ADDED: Import the publisher ---
from action_publisher import ActionPublisher, build_action_proposed

async def main():
    target_file = sys.argv[1] if len(sys.argv) > 1 else "tests/scenarios/prod_pack/case_22_storage_corruption_nuclear.json"
    file_path = Path(target_file)
    
    if not file_path.exists():
        print(f"Error: File {file_path} not found.")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    manager = DebateManager()
    res = await manager.run_async(payload)

    # --- ADDED: Integration Logic ---
    print("\n[INTEGRATION] Formatting and publishing result to Sandbox...")
    incident_id = payload.get("incident_id", file_path.stem)
    
    # Inject the original problem text so the Sandbox has context
    res["problem"] = payload.get("problem", "")
    
    # Build the official messaging envelope
    message = build_action_proposed(incident_id, res)
    
    # Fire the publisher (which drops it into sample_inputs)
    publisher = ActionPublisher()
    publisher.publish(message)
    # --------------------------------

    sol = res.get("solution", {})
    print("\n" + "=" * 60)
    print(f"               SINGLE CASE RESULT ({file_path.name})               ")
    print("=" * 60)
    print(f"Execution Tier:        {res.get('execution_tier')}")
    print(f"Confidence Score:      {res.get('confidence_score')}%")
    print(f"Safety Veto:           {res.get('safety_violation')}")
    print(f"Telemetry Hazard Flag: {sol.get('telemetry_hazard_detected', False)}")
    print(f"Total Latency:         {res.get('total_latency_seconds')}s")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())