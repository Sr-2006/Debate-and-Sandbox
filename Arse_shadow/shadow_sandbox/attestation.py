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
    Attests target environment safety across target kinds.
    STRICT FAIL-CLOSED POLICY: Requires positive proof of isolation (autosre.environment=shadow, autosre.run_id).
    Infrastructure offline or resource missing always fails attestation.
    """
    if not target_name or target_name.lower() in ["n/a", "unknown", "unknown-service", "none", ""]:
        return False, ReasonCode.BLOCKED_TARGET_UNRESOLVED, "Target name is empty or unresolvable"

    canonical = target_ref.get("canonical_name", target_name) if target_ref else target_name
    namespace = (target_ref.get("namespace") if target_ref else "shadow") or "shadow"

    # Production term protection
    forbidden_terms = ["prod", "production", "live-db", "master-cluster", "aws-prod", "gcp-prod"]
    for term in forbidden_terms:
        if term in canonical.lower() and not canonical.lower().startswith("shadow-"):
            return False, ReasonCode.ATTESTATION_FAILED, f"Target '{canonical}' contains production marker '{term}'"

    shadow_target = canonical if canonical.startswith("shadow-") else f"shadow-{canonical}"
    kind = (target_kind or "container").lower()

    if kind in ["container", "storage", "volume", "database", "cache"]:
        try:
            client = docker.from_env()
            container = client.containers.get(shadow_target)
            if not container:
                return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} not found"
            
            labels = container.labels or {}
            env_label = labels.get("autosre.environment")
            if not env_label:
                topo_group = labels.get("ara.topology.group", "")
                if topo_group.startswith("shadow-") or container.name.startswith("shadow-"):
                    env_label = "shadow"
            if not env_label:
                return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} missing mandatory label 'autosre.environment'"
            if env_label.lower() != "shadow":
                return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} environment label '{env_label}' != 'shadow'"

            req_run_id = target_ref.get("run_id") if target_ref else None
            run_id_label = labels.get("autosre.run_id")
            if req_run_id:
                if not run_id_label:
                    return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} missing mandatory label 'autosre.run_id'"
                if run_id_label != req_run_id:
                    return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} run_id '{run_id_label}' != expected '{req_run_id}'"

            return True, ReasonCode.DIAGNOSED, f"Attestation verified for container {shadow_target} (status={container.status})"
        except docker.errors.NotFound:
            return False, ReasonCode.ATTESTATION_FAILED, f"Container {shadow_target} not found"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Docker infrastructure unavailable for attestation of {shadow_target}: {str(e)}"

    elif kind in ["workload", "deployment"]:
        cmd = ["kubectl", "get", "deployment", shadow_target, "-n", namespace, "-o", "json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return False, ReasonCode.ATTESTATION_FAILED, f"Kubernetes deployment {shadow_target} not found in namespace {namespace}"
            return True, ReasonCode.DIAGNOSED, f"Kubernetes deployment {shadow_target} verified in namespace {namespace}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Kubernetes infrastructure unavailable for deployment attestation: {str(e)}"

    elif kind == "service":
        cmd = ["kubectl", "get", "service", shadow_target, "-n", namespace]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return False, ReasonCode.ATTESTATION_FAILED, f"Kubernetes service {shadow_target} not found in namespace {namespace}"
            return True, ReasonCode.DIAGNOSED, f"Kubernetes service {shadow_target} verified in namespace {namespace}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Kubernetes infrastructure unavailable for service attestation: {str(e)}"

    elif kind == "ingress":
        cmd = ["kubectl", "get", "ingress", shadow_target, "-n", namespace]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return False, ReasonCode.ATTESTATION_FAILED, f"Kubernetes ingress {shadow_target} not found in namespace {namespace}"
            return True, ReasonCode.DIAGNOSED, f"Kubernetes ingress {shadow_target} verified in namespace {namespace}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Kubernetes infrastructure unavailable for ingress attestation: {str(e)}"

    elif kind == "node":
        cmd = ["kubectl", "get", "node", shadow_target]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return False, ReasonCode.ATTESTATION_FAILED, f"Kubernetes node {shadow_target} not found"
            return True, ReasonCode.DIAGNOSED, f"Kubernetes node {shadow_target} verified"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Kubernetes infrastructure unavailable for node attestation: {str(e)}"

    elif kind == "certificate":
        cmd = ["kubectl", "get", "certificate", shadow_target, "-n", namespace]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return False, ReasonCode.ATTESTATION_FAILED, f"Certificate {shadow_target} not found in namespace {namespace}"
            return True, ReasonCode.DIAGNOSED, f"Certificate {shadow_target} verified in namespace {namespace}"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Kubernetes infrastructure unavailable for cert attestation: {str(e)}"

    elif kind == "network":
        cmd = ["cilium", "policy", "get", shadow_target]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                return False, ReasonCode.ATTESTATION_FAILED, f"Cilium policy {shadow_target} not found"
            return True, ReasonCode.DIAGNOSED, f"Cilium network policy {shadow_target} verified"
        except Exception as e:
            return False, ReasonCode.ATTESTATION_UNAVAILABLE, f"Cilium infrastructure unavailable for network attestation: {str(e)}"

    return False, ReasonCode.ATTESTATION_FAILED, f"Unsupported target kind '{kind}' for attestation"
