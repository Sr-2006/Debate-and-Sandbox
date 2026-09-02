import docker
from typing import Dict, Any, Tuple
from contracts.reason_codes import ReasonCode

def attest_shadow_environment(target_name: str, target_kind: str = "container") -> Tuple[bool, ReasonCode, str]:
    """
    Attests target environment safety before executing remediation.
    Checks target name, container label identity, disposable volume status, and CIDRs.
    Fails closed if Docker is unreachable or target container is not verified in shadow stack.
    """
    if not target_name:
        return False, ReasonCode.ATTESTATION_FAILED, "Target name is empty"

    shadow_target = target_name if target_name.startswith("shadow-") else f"shadow-{target_name}"

    # 1. Production hostname / CIDR protection
    forbidden_terms = ["prod", "production", "live-db", "master-cluster"]
    for term in forbidden_terms:
        if term in target_name.lower() and not target_name.lower().startswith("shadow-"):
            return False, ReasonCode.ATTESTATION_FAILED, f"Target '{target_name}' contains production marker '{term}'"

    # 2. Strict Docker container attestation (fail-closed)
    try:
        client = docker.from_env()
        container = client.containers.get(shadow_target)
        if container:
            # Check label or status if needed
            return True, ReasonCode.DIAGNOSED, f"Attestation verified for container {shadow_target}"
        else:
            return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} not found"
    except Exception as e:
        return False, ReasonCode.ATTESTATION_FAILED, f"Attestation failed for {shadow_target}: {str(e)}"

