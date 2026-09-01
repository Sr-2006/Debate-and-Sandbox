from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseVerifier(ABC):
    """Abstract Base Class for Operation-Specific Verifiers."""

    @abstractmethod
    def verify(self, target: str, action: str, parameters: Dict[str, Any], execution_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates operation-specific postconditions.
        Must return structured dict containing 'passed' (bool), 'target', 'verifier_name', and 'reason'.
        """
        pass
