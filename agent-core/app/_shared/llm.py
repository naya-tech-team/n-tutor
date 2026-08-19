"""The one file that decides which model an agent runs on.

Four call sites across this project, all of them `make_model()` or
`make_model(temperature=0.4)`. Keeping that signature is the point: swapping a
local llama for Bedrock is a change to *this* file, and to nothing else.
"""

from __future__ import annotations

from .config import settings

# What Strands falls back to when an Agent is built without `model=`.
#
# It is an Anthropic model, and Anthropic models on Bedrock need a use-case form
# approved per account — so the silent default surfaces as
#
#   ResourceNotFoundException: Model use case details have not been submitted
#   for this account.
#
# which names neither the model nor the agent that asked for it. Every Agent in
# this project passes `model=make_model()`; `tests/test_model_switch.py` checks
# that none stops doing so.
STRANDS_DEFAULT_IS_ANTHROPIC = True


def model_banner() -> str:
    """One line naming the model that will actually be used.

    Printed at start-up by every runtime, because `model=bedrock` — which is what
    they used to print — tells you the provider and hides the only value anyone
    ever needs to check. In CloudWatch this is the difference between reading the
    answer and redeploying to find out.
    """
    if settings.model_provider != "bedrock":
        return f"model=ollama:{settings.ollama_model}"
    return f"model=bedrock:{settings.bedrock_model_id or '(unset — make_model() will refuse)'}"


def make_model(temperature: float = 0.2):
    """Build the model that drives an agent's reasoning loop.

    Low temperature by default: we want the agent picking tools reliably, not
    writing creatively. Tool-calling accuracy on a small local model depends on
    it, and the outreach agent is the only caller that asks for more.
    """
    if settings.model_provider == "bedrock":
        # Imported lazily so a local run never needs boto3 installed.
        from strands.models import BedrockModel

        if not settings.bedrock_model_id:
            raise RuntimeError(
                "MODEL_PROVIDER=bedrock but BEDROCK_MODEL_ID is empty. Model ids are "
                "account- and region-specific — list the ones you can actually call with:\n"
                f"  aws bedrock list-foundation-models --region {settings.aws_region} "
                "--query 'modelSummaries[].modelId'\n"
                "then set BEDROCK_MODEL_ID in agent-core/.env."
            )
        return BedrockModel(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            temperature=temperature,
        )

    from strands.models.ollama import OllamaModel

    return OllamaModel(
        host=settings.ollama_host,
        model_id=settings.ollama_model,
        temperature=temperature,
    )
