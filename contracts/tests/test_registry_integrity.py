import pytest
import yaml
from pathlib import Path

def test_capabilities_yaml_validity():
    cap_path = Path(__file__).parent.parent / "capabilities.yaml"
    assert cap_path.exists(), "capabilities.yaml file must exist"
    
    with open(cap_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    assert "capabilities" in data
    caps = data["capabilities"]
    assert len(caps) >= 20, "Registry must contain at least 20 operations"
    
    for cap_name, cap_def in caps.items():
        assert "mode" in cap_def, f"{cap_name} missing 'mode'"
        assert cap_def["mode"] in ["OBSERVE", "SIMULATE", "MUTATE_REVERSIBLE", "MUTATE_HIGH_RISK"]
        assert "risk_class" in cap_def, f"{cap_name} missing 'risk_class'"
        assert cap_def["risk_class"] in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        assert "supported_targets" in cap_def, f"{cap_name} missing 'supported_targets'"
        assert "executor" in cap_def, f"{cap_name} missing 'executor'"
        assert "verifier" in cap_def, f"{cap_name} missing 'verifier'"
        assert "requires_human_approval" in cap_def, f"{cap_name} missing 'requires_human_approval'"
