from pydantic import BaseModel


class ChapterSpec(BaseModel):
    slug: str          # e.g. "01-overview" — becomes filename stem
    title: str
    description: str   # full writing brief for this chapter


class ChapterPlan(BaseModel):
    chapters: list[ChapterSpec]


class CriterionResult(BaseModel):
    verdict: str        # "pass" or "fail"
    notes: str
    revisions: list[str]


class ThoroughnessReview(BaseModel):
    chapter_title: str
    criteria: dict[str, CriterionResult]
    overall_verdict: str    # "pass" or "revise"
    revision_summary: str   # empty string when overall_verdict == "pass"
