import requests

from config import OLLAMA_URL, OLLAMA_MODEL, REQUEST_TIMEOUT, MAX_REPLY_TOKENS


def get_llm_reply(messages):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "num_predict": MAX_REPLY_TOKENS
        }
    }

    response = requests.post(
        OLLAMA_URL, 
        json=payload, 
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()

    return response.json()["message"]["content"]