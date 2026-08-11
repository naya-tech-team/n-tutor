"""The one model every example runs on."""

from strands.models.ollama import OllamaModel

from .config import settings


def make_model(temperature: float = 0.2) -> OllamaModel:
    """Build the local llama that drives an agent's reasoning loop.

    Low temperature by default: we want the agent picking tools reliably, not
    writing creatively. Tool-calling accuracy on a small local model depends on it.
    """
    return OllamaModel(
        host=settings.ollama_host,
        model_id=settings.ollama_model,
        temperature=temperature,
    )
