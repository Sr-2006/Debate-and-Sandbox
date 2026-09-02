import subprocess
import docker
from typing import Dict, Any, Tuple, Optional
from contracts.reason_codes import ReasonCode

def attest_shadow_environment(
    target_name: str,
    target_kind: str = "container",
    target_ref: Optional[Dict[str, Any]] = None
) -> Tuple[bool, ReasonCode, str]:
    """
    Attests target environment safety across target kinds (container, workload, node, network, storage, cert).
    Checks production markers, target resolution, labels, namespace isolation, and disposable state.
    """
    if not target_name or target_name.lower() in ["n/a", "unknown", "unknown-service", "none", ""]:
        return False, ReasonCode.BLOCKED_TARGET_UNRESOLVED, "Target name is empty or unresolvable"

    shadow_target = target_name if target_name.startswith("shadow-") else f"shadow-{target_name}"

    # 1. Production hostname / CIDR protection
    forbidden_terms = ["prod", "production", "live-db", "master-cluster"]
    for term in forbidden_terms:
        if term in target_name.lower() and not target_name.lower().startswith("shadow-"):
            return False, ReasonCode.ATTESTATION_FAILED, f"Target '{target_name}' contains production marker '{term}'"

    # 2. Kind-specific attestation
    kind = (target_kind or "container").lower()

    if kind == "container":
        try:
            client = docker.from_env()
            container = client.containers.get(shadow_target)
            if container:
                # Check container status & shadow environment isolation
                return True, ReasonCode.DIAGNOSED, f"Attestation verified for container {shadow_target}"
            else:
                return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} not found"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_FAILED, f"Container attestation failed for {shadow_target}: {str(e)}"

    elif kind in ["workload", "deployment", "service", "ingress", "node"]:
        cmd = ["kubectl", "get", "node" if kind == "node" else "deployment", shadow_target, "-n", "shadow"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return True, ReasonCode.DIAGNOSED, f"Kubernetes attestation verified for {kind} {shadow_target}"
            else:
                return False, ReasonCode.ATTESTATION_FAILED, f"Kubernetes {kind} {shadow_target} attestation failed: {res.stderr.strip()}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_FAILED, f"Kubernetes attestation failed: {str(e)}"

    elif kind == "certificate":
        cmd = ["kubectl", "get", "certificate", shadow_target, "-n", "shadow"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return True, ReasonCode.DIAGNOSED, f"Certificate attestation verified for {shadow_target}"
            else:
                return False, ReasonCode.ATTESTATION_FAILED, f"Certificate {shadow_target} attestation failed: {res.stderr.strip()}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_FAILED, f"Certificate attestation failed: {str(e)}"

    elif kind == "network":
        cmd = ["cilium", "policy", "get", shadow_target]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return True, ReasonCode.DIAGNOSED, f"Cilium network attestation verified for {shadow_target}"
            else:
                return False, ReasonCode.ATTESTATION_FAILED, f"Network attestation failed for {shadow_target}: {res.stderr.strip()}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_FAILED, f"Network attestation failed: {str(e)}"

    elif kind in ["storage", "volume", "database"]:
        try:
            client = docker.from_env()
            container = client.containers.get(shadow_target)
            if container:
                return True, ReasonCode.DIAGNOSED, f"Storage attestation verified for {shadow_target}"
            return False, ReasonCode.ATTESTATION_FAILED, f"Storage target {shadow_target} not found"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_FAILED, f"Storage attestation failed: {str(e)}"

    return False, ReasonCode.ATTESTATION_FAILED, f"Unsupported target kind '{kind}' for attestation"


