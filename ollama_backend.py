# Accept chat messages and return response text; the loop validates bilingual fields.
import requests
import config
from conversation import reply_schema, with_reply_instructions
from backend_errors import BackendError


def get_llm_reply(messages):
    get_llm_reply.last_usage = {"provider": "ollama", "model": config.OLLAMA_MODEL}
    payload = {
        "think": False, "stream": False, "model": config.OLLAMA_MODEL, "messages": with_reply_instructions(messages),
        # A schema constrains structure, not accuracy or translation equivalence.
        "format": reply_schema(),
        "options": {"num_predict": config.MAX_REPLY_TOKENS},
    }
    try:
        response = requests.post(config.OLLAMA_URL, json=payload, timeout=config.REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout as error:
        raise BackendError("Ollama took too long to respond.") from error
    except requests.exceptions.ConnectionError as error:
        raise BackendError("Cannot connect to Ollama. Check that it is running.") from error
    except requests.exceptions.RequestException as error:
        raise BackendError(f"Ollama request failed: {error}") from error
    try:
        data = response.json()
        get_llm_reply.last_usage.update({
            "model": data.get("model") or config.OLLAMA_MODEL,
            "input_tokens": data.get("prompt_eval_count"),
            "cached_input_tokens": data.get("prompt_eval_cached_count"),
            "output_tokens": data.get("eval_count"),
            "estimated_api_cost_usd": 0.0,
        })
        if data.get("done_reason") == "length":
            raise BackendError("Ollama reached the reply token limit. Try a shorter reply or raise MAX_REPLY_TOKENS.")
        content = data["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Missing response text.")
        return content
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise BackendError("Ollama returned an invalid response.") from error
