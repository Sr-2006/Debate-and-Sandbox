from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseExecutor(ABC):
    """Abstract Base Class for Operation-Specific Executors."""

    @abstractmethod
    def execute(self, target: str, action: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a typed operation against a target container/workload.
        Must return a structured dictionary containing 'success' (bool), 'target', 'tool', and 'output'.
        """
        pass
