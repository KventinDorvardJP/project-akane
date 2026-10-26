import requests
from pathlib import Path

from ollama_backend import get_llm_reply

def main():
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
            reply = get_llm_reply(messages)

        except requests.exceptions.ConnectionError:
            messages.pop()
            print("Cannot connect to Ollama. Check that it is running.")
            continue
        except requests.exceptions.Timeout:
            messages.pop()
            print("Ollama took too long to respond. Try again.")
            continue
        except requests.exceptions.HTTPError as error:
            messages.pop()
            print(f"Ollama rejected the request: {error}")
            continue

        messages.append({"role": "assistant", "content": reply})
        print(f"Akane: {reply}")

if __name__ == "__main__":
    main()