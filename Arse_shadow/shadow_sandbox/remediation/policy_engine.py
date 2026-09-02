import yaml
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
from contracts.reason_codes import ReasonCode

class PolicyEngine:
    """Fail-closed policy engine driven by contracts/capabilities.yaml."""

    def __init__(self, capabilities_path: Optional[Path] = None):
        if capabilities_path is None:
            capabilities_path = Path(__file__).resolve().parents[3] / "contracts" / "capabilities.yaml"
        
        self.capabilities = {}
        if capabilities_path.exists():
            with open(capabilities_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                self.capabilities = data.get("capabilities", {})

    def evaluate_intent(self, intent: Dict[str, Any], target_ref: Dict[str, Any]) -> Tuple[bool, ReasonCode, str]:
        """
        Evaluates an intent against the capability policy.
        Returns: (allowed: bool, reason_code: ReasonCode, message: str)
        """
        intent_type = intent.get("intent_type")
        if not intent_type or intent_type not in self.capabilities:
            return False, ReasonCode.BLOCKED_UNKNOWN_CAPABILITY, f"Intent '{intent_type}' is not registered in capabilities catalog"

        cap_def = self.capabilities[intent_type]

        # Check target resolution
        target_kind = target_ref.get("kind", "container")
        supported_targets = cap_def.get("supported_targets", [])
        if supported_targets and not is_target_supported(supported_targets, target_kind):
            return False, ReasonCode.BLOCKED_TARGET_UNRESOLVED, f"Target kind '{target_kind}' not supported for capability '{intent_type}'"

        # Check human approval requirement
        if cap_def.get("requires_human_approval", False) or intent.get("requires_human_approval", False):
            return False, ReasonCode.REQUIRES_HUMAN_APPROVAL, f"Capability '{intent_type}' is high-risk and requires human approval"

        return True, ReasonCode.DIAGNOSED, "Policy check passed"

def is_target_supported(supported_list: List[str], target_kind: str) -> bool:
    if not supported_list:
        return True
    return target_kind in supported_list

