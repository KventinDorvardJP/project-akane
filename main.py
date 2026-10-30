from backend_errors import BackendError
from pathlib import Path

from ollama_backend import get_llm_reply
from config import MAX_HISTORY_TURNS

def main(reply_function):
    personality_path = Path(__file__).resolve().parent / "personality.txt"
 
    try:
        system_prompt = personality_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Personality file not found: {personality_path}")
        return

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    while True:
        test_message = input("You: ").strip()
        match test_message.lower():
            case "exit":
                break
            case "reset":
                messages = messages[:1]
                print("Conversation history reset.")
                continue
            case "":
                continue

        messages.append({"role": "user", "content": test_message})
        try:
            reply = reply_function(messages)

        except BackendError as error:
            messages.pop()
            print(error)
            continue

        messages.append({"role": "assistant", "content": reply})
        while len(messages) > 1 + MAX_HISTORY_TURNS * 2:
            del messages[1:3]
        print(f"Akane: {reply}")

if __name__ == "__main__":
    main(get_llm_reply)