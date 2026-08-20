from src.brain import get_response
from src.memory_store import load_memory, save_memory
from src.voice import listen, speak



def choose_mode():
    print("\nAto Mode Selection:")
    print("1. Text Mode")
    print("2. Voice Mode")

    choice = input("Choose 1 or 2: ")

    return "voice" if choice == "2" else "text"


def run():
    # load persistent memory
    memory = load_memory()

    mode = choose_mode()

    print(f"\nAto started in {mode.upper()} mode\n")

    if mode == "voice":
        speak("Voice mode activated")

    while True:

        # INPUT
        if mode == "text":
            user_input = input("You: ")
        else:
            user_input = listen()

        if not user_input:
            continue

        # EXIT
        if user_input.lower() in ["exit", "quit", "stop"]:
            save_memory(memory)

            if mode == "voice":
                speak("Shutting down")
            else:
                print("Ato: shutting down")

            break

        # BRAIN RESPONSE
        response = get_response(user_input, memory)

        # SAVE MEMORY AFTER EVERY TURN
        save_memory(memory)

        # OUTPUT
        if mode == "voice":
            speak(response)
        else:
            print("Ato:", response)


if __name__ == "__main__":
    run()