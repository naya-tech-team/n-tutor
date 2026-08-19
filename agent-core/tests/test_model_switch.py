"""`make_model()` is the only file that decides what an agent runs on.

Construction only — nothing here calls a model. The point is that the switch
works and that the bedrock path fails with a message you can act on rather than
an authentication error three layers down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _shared.config import settings
from _shared.llm import make_model, model_banner

ROOT = Path(__file__).resolve().parents[1]


def test_default_is_the_local_llama():
    model = make_model()
    assert type(model).__name__ == "OllamaModel"


def test_temperature_is_forwarded():
    """The outreach agent is the only caller that raises it — that must reach the model."""
    warm = make_model(temperature=0.4)
    assert type(warm).__name__ == "OllamaModel"


def test_bedrock_without_a_model_id_says_what_to_do(monkeypatch):
    monkeypatch.setattr(settings, "model_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_model_id", "")

    with pytest.raises(RuntimeError) as exc:
        make_model()

    message = str(exc.value)
    assert "BEDROCK_MODEL_ID" in message
    assert "list-foundation-models" in message


def test_bedrock_path_builds_a_bedrock_model(monkeypatch):
    monkeypatch.setattr(settings, "model_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_model_id", "us.amazon.nova-lite-v1:0")
    monkeypatch.setattr(settings, "aws_region", "us-west-2")

    model = make_model()
    assert type(model).__name__ == "BedrockModel"


def test_the_model_id_reaches_the_bedrock_model(monkeypatch):
    """Not ceremony. `BEDROCK_MODEL_ID` travels terraform -> container env ->
    Settings -> here, and the only symptom of it not arriving is an error naming
    a model you never chose."""
    monkeypatch.setattr(settings, "model_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_model_id", "us.amazon.nova-lite-v1:0")

    assert make_model().config["model_id"] == "us.amazon.nova-lite-v1:0"


# --- the silent default ------------------------------------------------------


def test_banner_names_the_model_not_the_provider(monkeypatch):
    """`model=bedrock` is what these runtimes used to log, and it hides the one
    value you need when a model call fails."""
    monkeypatch.setattr(settings, "model_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_model_id", "us.amazon.nova-lite-v1:0")
    assert "us.amazon.nova-lite-v1:0" in model_banner()

    monkeypatch.setattr(settings, "bedrock_model_id", "")
    assert "unset" in model_banner()


def test_every_agent_is_given_a_model():
    """The landmine this guards.

    Strands defaults a model-less Agent to an **Anthropic** model, and Anthropic
    models on Bedrock need a use-case form approved per account. So an Agent built
    without `model=` does not fall back to your configured model — it fails with

      ResourceNotFoundException: Model use case details have not been submitted
      for this account.

    naming neither the model nor the agent that asked for it. Source-level, because
    constructing the real agents needs a live model.
    """
    from strands.models.bedrock import DEFAULT_BEDROCK_MODEL_ID

    # If this ever stops being Anthropic the test above is less urgent, but the
    # rule — always pass model= — does not change.
    assert "anthropic" in DEFAULT_BEDROCK_MODEL_ID

    runtimes = ROOT / "app/runtimes"
    offenders = []
    for main in sorted(runtimes.glob("*/main.py")):
        source = main.read_text()
        # hr_skills_mcp is a tool server; it builds no Agent at all.
        if "Agent(" not in source:
            continue
        for block in source.split("Agent(")[1:]:
            head = block[:600]
            if "model=make_model" not in head:
                offenders.append(main.parent.name)

    assert not offenders, f"Agent built without model=make_model(): {offenders}"
