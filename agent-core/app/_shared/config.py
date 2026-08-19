"""One place every runtime reads its settings from.

This project runs in two modes from one codebase:

  local      Ollama for the model, the dataset in `hr_data.py`, agents on
             127.0.0.1 ports — exactly what `a2a-strands/` does today.
  agentcore  Bedrock for the model, the dataset in S3, agents behind ARNs.

`agentcore` is the switch that decides how each entrypoint binds a port.
`model_provider` and `data_source` are deliberately separate from it, so you can
run locally against real S3 data, or run a Bedrock model against the in-process
dataset, while you are working out which half broke.

Values come from `agent-core/.env`, and a real environment variable beats it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# _shared -> app -> agent-core
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "dev"] = "local"
    log_level: str = "INFO"

    # --- which half of the world are we in? ---------------------------------
    # True only inside an AgentCore Runtime container. It changes how the
    # entrypoint binds — ports and mount paths are the service's contract, not
    # ours — and nothing above `if __name__ == "__main__":` in any runtime.
    agentcore: bool = False

    # --- the model ----------------------------------------------------------
    model_provider: Literal["ollama", "bedrock"] = "ollama"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # No default on purpose. Model ids are account- and region-specific, and a
    # hardcoded one that is not enabled fails deep inside a bedrock call with a
    # message about the wrong thing. make_model() checks this and says so.
    bedrock_model_id: str = ""
    aws_region: str = "us-west-2"

    # --- the data -----------------------------------------------------------
    data_source: Literal["local", "s3"] = "local"
    s3_bucket: str = ""

    # --- what the agents talk to -------------------------------------------
    # Local: the three A2A specialists, on the ports a2a-strands uses, so both
    # projects can be read side by side. On AgentCore all three collapse onto
    # 9000 and the ARN becomes the address.
    screening_url: str = "http://127.0.0.1:9001"
    outreach_url: str = "http://127.0.0.1:9002"
    compliance_url: str = "http://127.0.0.1:9007"

    # Filled in by terraform outputs once deployed.
    gateway_url: str = ""
    skills_mcp_arn: str = ""
    screening_arn: str = ""
    outreach_arn: str = ""
    compliance_arn: str = ""
    memory_id: str = ""

    # --- local scratch ------------------------------------------------------
    run_dir: Path = ROOT / ".run"

    @property
    def seed_dir(self) -> Path:
        """Where scripts/seed_s3.py writes the JSON before uploading it.

        This is also where `terraform/01_s3_data` reads it from, via its
        `seed_dir` variable. Two places pointing at one directory: if you move
        it, move both, or the apply uploads whatever was there last.
        """
        return ROOT / "terraform" / "01_s3_data" / "seed"


settings = Settings()
