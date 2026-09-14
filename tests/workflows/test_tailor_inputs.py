"""TailorInputs snapshots everything a durable run needs."""

import pytest

from sira.models.workflow import RunMetadata, TailorInputs
from sira.workflows import ResumeTailorWorkflow
from sira.workflows import agents as agents_mod


def test_build_inputs_snapshots_loop_and_gate_settings():
    agents_mod.set_quality_gate(enabled=False, threshold=9)
    wf = ResumeTailorWorkflow(write_attempts=3, review_iterations=2, interactive=True)
    inputs = wf.build_inputs(
        "# resume", job_content="job", model="openai:x", debug=True
    )
    assert inputs.resume_text == "# resume"
    assert inputs.job_content == "job"
    assert inputs.model == "openai:x"
    assert inputs.write_attempts == 3
    assert inputs.review_iterations == 2
    assert inputs.interactive is True
    assert inputs.debug is True
    assert inputs.quality_gate is False
    assert inputs.gate_threshold == 9
    assert inputs.metadata == RunMetadata()


def test_build_inputs_records_model_tiers_only_when_configured():
    wf = ResumeTailorWorkflow()
    assert wf.build_inputs("r", job_content="j").fast_model is None
    agents_mod.set_agent_models(fast="openai:fast", strong="openai:strong")
    inputs = wf.build_inputs("r", job_content="j")
    assert (inputs.fast_model, inputs.strong_model) == ("openai:fast", "openai:strong")


def test_tailor_inputs_round_trip_through_pickle(sample_cv):
    import pickle

    inputs = TailorInputs(
        resume_text="r",
        job_content="j",
        pre_parsed_cv=sample_cv,
        metadata=RunMetadata(job_url="https://x", output_dir="/tmp/out"),
    )
    assert pickle.loads(pickle.dumps(inputs)) == inputs


@pytest.mark.anyio
async def test_run_durable_requires_an_active_runtime(monkeypatch):
    import sira.workflows as wf

    monkeypatch.setattr(wf, "is_active", lambda: False)
    with pytest.raises(RuntimeError, match="durable_runtime"):
        await ResumeTailorWorkflow().run("r", job_content="j")
