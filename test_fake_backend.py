# Deterministic replacement for an LLM, satisfying the bilingual JSON contract.
import json


def get_llm_reply(messages):
    latest_message = messages[-1]["content"]
    return json.dumps(
        {"ja": f"受信したメッセージ: {latest_message}",
         "en": f"Received message: {latest_message}"},
        ensure_ascii=False,
    )
