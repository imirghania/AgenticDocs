from typing import Optional

from pydantic import BaseModel, Field, model_validator


class PackageIntent(BaseModel):
    package_name: str = Field(description="The name of the software package or library")
    language: str = Field(description="The programming language the package belongs to (e.g., Python, JS, Rust)")
    ecosystem: str = Field(description="The package manager or ecosystem (e.g., pypi, npm, crates.io)")
    hints: list[str] = Field(description="Any extra context the user gave")


class PackageSelectionResult(BaseModel):
    action: str  # "select" | "none" | "clarify"
    selected_index: Optional[int] = None        # 0-based; only when action == "select"
    new_package_name: Optional[str] = None      # only when action == "none"
    clarification_question: Optional[str] = None  # only when action == "clarify"

    @model_validator(mode="after")
    def check_consistency(self) -> "PackageSelectionResult":
        if self.action == "select":
            assert self.selected_index is not None, \
                "selected_index required when action is 'select'"
        elif self.action == "none":
            assert self.new_package_name is not None, \
                "new_package_name required when action is 'none'"
        elif self.action == "clarify":
            assert self.clarification_question is not None, \
                "clarification_question required when action is 'clarify'"
        else:
            raise ValueError(f"Unknown action: {self.action}")
        return self
