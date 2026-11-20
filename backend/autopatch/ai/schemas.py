"""What the AI must return. Each class is sent to the model as a JSON schema (structured
outputs), so it stays within what that feature supports: no numeric or string constraints,
no recursion, no free-form dictionaries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["crash", "incorrect_output", "edge_case", "security", "performance", "state_corruption"]
Severity = Literal["critical", "high", "medium", "low"]
TestKind = Literal["reproduce", "normal", "invalid", "minimum", "maximum", "edge"]


class AIFinding(BaseModel):
    title: str = Field(description="Short name for the bug, under 80 characters.")
    file: str = Field(description="File path exactly as shown in the project listing.")
    function: str = Field(description="Function or method name, or '<module>' for top-level code.")
    line_start: int
    line_end: int
    category: Category
    severity: Severity
    confidence: float = Field(description="0.0 to 1.0, calibrated as described in the instructions.")
    description: str = Field(description="What observably goes wrong: crash, wrong value, security impact.")
    root_cause: str = Field(description="Why it happens, traced from the input through the code to the fault.")
    trigger: str = Field(description="Concrete input or situation that triggers it, e.g. get_grade(-5).")
    expected_behavior: str = Field(description="What the code should do for that input instead.")
    severity_reason: str = Field(description="One sentence justifying the severity.")


class AIScanResult(BaseModel):
    project_summary: str = Field(description="Two or three sentences on what the project does.")
    findings: list[AIFinding]


class RootCauseAnalysis(BaseModel):
    is_real_bug: bool
    title: str
    description: str = Field(description="What observably goes wrong.")
    root_cause: str = Field(description="Step-by-step cause, from the triggering input to the failure.")
    trigger: str = Field(description="The exact inputs or conditions that trigger the bug.")
    expected_behavior: str
    related_code: list[str] = Field(description="Other functions or files involved, each with how it is involved.")
    impact: str = Field(description="Who or what is affected and how badly.")
    category: Category
    severity: Severity
    confidence: float
    severity_reason: str


class GeneratedCase(BaseModel):
    name: str = Field(description="Test function name exactly as written in the code.")
    kind: TestKind
    description: str = Field(description="One line: what this test checks.")


class GeneratedTests(BaseModel):
    test_code: str = Field(description="A complete pytest module.")
    cases: list[GeneratedCase]


class CodeEdit(BaseModel):
    original_code: str = Field(
        description="Exact contiguous whole lines copied from the current file, without the 'N| ' line-number prefix."
    )
    patched_code: str = Field(description="Replacement for original_code, with the same indentation.")


class FixProposal(BaseModel):
    file: str
    edits: list[CodeEdit]
    explanation: str = Field(description="Why this change fixes the root cause.")
    behavior_change: str = Field(description="What callers will notice, especially for inputs that used to be accepted.")
