import json
import os

MEMORY_FILE = "data/memory.json"


def load_memory():
    """Load memory from file"""
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)


def save_all(memory):
    """Save full memory back to file"""
    os.makedirs("data", exist_ok=True)
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)


def save_memory(key, value):
    """Store a single piece of memory"""
    memory = load_memory()
    memory[key] = value
    save_all(memory)


def get_memory(key):
    """Retrieve a single piece of memory"""
    memory = load_memory()
    return memory.get(key)

# src/memory.py

memory = {
    "name": None,
    "last_message": None
}