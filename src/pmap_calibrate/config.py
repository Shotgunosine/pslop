"""Find the OpenRouter API key and model from the environment or a .env file."""

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

API_KEY_VAR = "OPENROUTER_API_KEY"
MODEL_VAR = "PMAP_MODEL"
DEFAULT_MODEL = "typesafe/jev-1.13"
USER_ENV_FILE = Path.home() / ".config" / "pmap-calibrate" / "env"


class MissingKeyError(Exception):
    pass


@dataclass
class Settings:
    api_key: str | None
    key_source: str | None
    model: str
    model_source: str
    env_file: Path | None
    warnings: list[str] = field(default_factory=list)

    def require_key(self) -> str:
        if self.api_key:
            return self.api_key
        searched = str(self.env_file) if self.env_file else f"./.env or {USER_ENV_FILE}"
        raise MissingKeyError(
            f"No OpenRouter API key found. Set {API_KEY_VAR} in your shell, or put "
            f"{API_KEY_VAR}=... in {searched} (see .env.example), or pass --env-file PATH."
        )


def load_settings(env_file: Path | None = None, model: str | None = None) -> Settings:
    """Read settings. Precedence: command-line option > environment variable > env file > default.

    Without `env_file`, the first existing file of ./.env and ~/.config/pmap-calibrate/env is used.
    """
    candidates = [env_file] if env_file else [Path.cwd() / ".env", USER_ENV_FILE]
    used = next((path for path in candidates if path.is_file()), None)
    if env_file and used is None:
        raise FileNotFoundError(f"Env file not found: {env_file}")
    file_values = {k: v for k, v in (dotenv_values(used) if used else {}).items() if v}
    warnings = []
    if used and stat.S_IMODE(used.stat().st_mode) & 0o077:
        warnings.append(f"{used} is readable by other users; run: chmod 600 '{used}'")

    if os.environ.get(API_KEY_VAR):
        api_key, key_source = os.environ[API_KEY_VAR], f"environment variable {API_KEY_VAR}"
    elif file_values.get(API_KEY_VAR):
        api_key, key_source = file_values[API_KEY_VAR], str(used)
    else:
        api_key, key_source = None, None

    if model:
        model_source = "--model option"
    elif os.environ.get(MODEL_VAR):
        model, model_source = os.environ[MODEL_VAR], f"environment variable {MODEL_VAR}"
    elif file_values.get(MODEL_VAR):
        model, model_source = file_values[MODEL_VAR], str(used)
    else:
        model, model_source = DEFAULT_MODEL, "default"
    return Settings(api_key, key_source, model, model_source, used, warnings)
