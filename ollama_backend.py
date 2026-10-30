import requests

from config import OLLAMA_URL, OLLAMA_MODEL, REQUEST_TIMEOUT, MAX_REPLY_TOKENS
from backend_errors import BackendError



def get_llm_reply(messages):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "num_predict": MAX_REPLY_TOKENS
        }
    }

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as error:
        raise BackendError("Ollama took too long to respond.") from error
    except requests.exceptions.ConnectionError as error:
        raise BackendError(
            "Cannot connect to Ollama. Check that it is running."
        ) from error
    except requests.exceptions.HTTPError as error:
        raise BackendError(
            f"Ollama rejected the request: {error}"
        ) from error

    return response.json()["message"]["content"]