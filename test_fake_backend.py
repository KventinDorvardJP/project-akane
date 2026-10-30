def get_llm_reply(messages):
    latest_message = messages[-1]["content"]
    return f"Test backend received: {latest_message}"