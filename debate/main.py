import asyncio
from debate_manager import DebateManager

manager = DebateManager()

# Structured Incident Input Payload
incident_payload = {
  "system_context": {
    "objective": "Perform automated Multi-Agent Root Cause Analysis (RCA) and recommend corrective actions.",
    "environment": "Dockerized Microservices (Java/Spring Boot, PostgreSQL, Redis, RabbitMQ, OpenTelemetry)",
    "current_health_score": 70,
    "active_warnings": 4
  },
  "incident_event": {
    "incident_id": "api-gateway_2",
    "target_service": "api-gateway",
    "priority_score": 79.65,
    "severity": "CRITICAL",
    "occurrence_count": 97
  },
  "infrastructure_topology": {
    "role": "edge-routing-and-rate-limiting",
    "downstream_dependencies": [
      "auth-service",
      "order-service",
      "payment-service",
      "otel-collector"
    ],
    "exposed_ports": ["8080:8080"]
  },
  "service_health_status": {
    "docker_status": "running",
    "health_check": "healthy",
    "dependency_states": {
      "otel-collector": {
        "status": "exited",
        "health": "unhealthy"
      }
    }
  },
  "telemetry_evidence": {
    "log_cluster_template": "<NUM>-<NUM>-<NUM>T<NUM>:<NUM>:<NUM>.<NUM>Z ERROR <NUM> --- [api-gateway] [lector:<NUM>/...] [ ] i.o.exporter.internal.http.HttpExporter : Failed to export spans. The request could not be executed. Full error message: Connection reset",
    "log_samples": [
      {
        "timestamp": "2026-08-08T14:09:50.797752",
        "level": "ERROR",
        "content": "java.net.SocketException: Connection reset\n\tat java.base/sun.nio.ch.NioSocketImpl.implRead...",
        "trace_id": None,
        "span_id": None
      }
    ],
    "metrics_snapshot": [
      {
        "timestamp": "2026-08-08T14:41:49.192396",
        "cpu_percent": 16.28,
        "memory_usage_bytes": 299728896,
        "memory_usage_percent": 3.59
      }
    ]
  },
  "injected_chaos_context": {
    "active_infrastructure_mutations": "Infrastructure orchestrator triggered a 30-second pause on dependency."
  },
  "agent_instruction": "Analyze the provided telemetry evidence and dependency states. Determine the root cause of the 'Connection reset' failure in the api-gateway and output a remediation plan."
}

async def main():
    print("Initializing Qwen-2.5-3B Tri-Modal Debate System (Genius Async Core)...")
    result = await manager.run_async(incident_payload)
    
    print("\n" + "=" * 60)
    print("           QWEN-2.5-3B TRI-MODAL DEBATE SUMMARY           ")
    print("=" * 60)
    print(f"Total Pipeline Latency:  {result['total_latency_seconds']} seconds")
    print(f"Consensus Score:         {result['consensus_score']} (Threshold: 0.65)")
    print(f"Round 2 Debated:         {'Yes' if result['round_2_executed'] else 'No (Genius Async Optimization)'}")
    print(f"Orchestrator Confidence: {result['confidence_score']}%")
    print("=" * 60)
    print("\nFINAL TECHNICAL SOLUTION:\n")
    print(result["solution"])

if __name__ == "__main__":
    asyncio.run(main())
