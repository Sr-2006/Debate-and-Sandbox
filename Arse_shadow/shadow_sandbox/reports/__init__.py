"""
shadow_sandbox/reports

Layer 4: Outcome record report generator.
"""
from shadow_sandbox.reports.report_generator import (
    generate_phase34_report,
    ReportContractError,
    ReportAtomicityError,
)

__all__ = ["generate_phase34_report", "ReportContractError", "ReportAtomicityError"]
