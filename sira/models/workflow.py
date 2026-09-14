from pydantic import BaseModel, Field

from sira.models.agents.output import CV, FinalReport


class ResumeTailorResult(BaseModel):
    company_name: str
    job_title: str
    tailored_resume: str
    audit_report: dict
    passed: bool
    final_report: FinalReport | None = None


class RunMetadata(BaseModel):
    """What the CLI needs after the workflow returns (output files, memory).

    Stored with the run as part of TailorInputs, so ``sira resume <run-id>``
    needs no other arguments.
    """

    job_url: str | None = None
    # Set for re-tailor runs: the prior job record to update in memory.
    job_id: str | None = None
    resume_source_path: str = ""
    output_dir: str = "./output"
    output_pattern: str = "{company_name}-{job_title}"
    resume_name_pattern: str = "{company_name}-{full_name}"
    # The scraped (or stored) posting, saved to memory together with the result.
    job_posting_markdown: str = ""


class TailorInputs(BaseModel):
    """Everything one durable run needs. DBOS stores it as the workflow input.

    Model tiers and quality-gate settings are snapshotted here so a continued
    run is configured exactly like the first one.
    """

    resume_text: str
    job_content: str | None = None
    job_content_file_path: str | None = None
    pre_parsed_cv: CV | None = None
    model: str | None = None
    fast_model: str | None = None
    strong_model: str | None = None
    write_attempts: int = 2
    review_iterations: int = 1
    quality_gate: bool = True
    gate_threshold: int = 6
    interactive: bool = False
    debug: bool = False
    verbose: bool = False
    metadata: RunMetadata = Field(default_factory=RunMetadata)
