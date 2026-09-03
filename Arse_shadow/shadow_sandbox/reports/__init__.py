"""
shadow_sandbox/reports

Layer 4: Outcome record report generator.
"""
from shadow_sandbox.reports.report_generator import generate_phase34_report, ReportContractError
from shadow_sandbox.reports.legacy_report_generator import generate_report, generate_mvp_report

__all__ = ["generate_phase34_report", "ReportContractError", "generate_report", "generate_mvp_report"]
