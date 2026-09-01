from pydantic import BaseModel

from sira.models.agents.output import FinalReport


class ResumeTailorResult(BaseModel):
    company_name: str
    job_title: str
    tailored_resume: str
    audit_report: dict
    passed: bool
    final_report: FinalReport | None = None
