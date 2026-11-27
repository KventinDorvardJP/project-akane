# Select a provider once. There is no silent fallback to a different paid/local model.
import os
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

import config
from backend_errors import BackendError


class UnavailableBackend:
    """Keep the GUI/settings usable after a conversation startup failure."""
    def __init__(self, message):
        self.startup_error = message

    def __call__(self, messages):
        raise BackendError(self.startup_error)


def select_reply_backend(name, project_dir):
    if name == "ollama":
        from ollama_backend import get_llm_reply
        return get_llm_reply
    if name == "openai":
        from openai_backend import OpenAIBackend
        load_dotenv(project_dir / ".env")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise BackendError("OPENAI_API_KEY is required for the OpenAI conversation backend.")
        try:
            client = OpenAI(api_key=api_key, timeout=config.OPENAI_REQUEST_TIMEOUT, max_retries=0)
            return OpenAIBackend(client)
        except (OpenAIError, ValueError) as error:
            raise BackendError(f"Cannot initialize the OpenAI conversation backend: {error}") from error
    raise BackendError(f"Unsupported conversation backend: {name}")
