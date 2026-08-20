import json
import os

FILE_PATH = "memory.json"


def load_memory():
    if os.path.exists(FILE_PATH):
        with open(FILE_PATH, "r") as f:
            return json.load(f)

    return {
        "name": None,
        "chat_history": []
    }


def save_memory(memory):
    with open(FILE_PATH, "w") as f:
        json.dump(memory, f, indent=2)