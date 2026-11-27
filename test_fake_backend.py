# Deterministic replacement for an LLM, satisfying the bilingual JSON contract.
import json
import config


def get_llm_reply(messages):
    latest_message = messages[-1]["content"]
    language = config.SUBTITLE_LANGUAGE
    prefix = "Получено сообщение" if language == "ru" else "Received message"
    part = {"ja": f"受信したメッセージ: {latest_message}", language: f"{prefix}: {latest_message}"}
    return json.dumps({"segments": [part]}, ensure_ascii=False)
