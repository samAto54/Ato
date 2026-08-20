from src.ai import ask_deepseek


def local_brain(user_input, memory):
    user_input_lower = user_input.lower()

    # store chat history
    memory["chat_history"].append({"user": user_input})

    if "my name is" in user_input_lower:
        name = user_input_lower.replace("my name is", "").strip()
        memory["name"] = name
        return f"Got it. I'll remember your name, {name}."

    if "what is my name" in user_input_lower:
        return f"It's {memory['name']}" if memory.get("name") else "I don't know yet."

    if user_input_lower in ["hi", "hello", "hey"]:
        return "Hey. I'm Ato. I'm here."

    return None


def get_response(user_input, memory):
    response = local_brain(user_input, memory)

    if response:
        memory["chat_history"].append({"ato": response})
        return response

    ai_response = ask_deepseek(user_input, memory)

    memory["chat_history"].append({"ato": ai_response})
    return ai_response