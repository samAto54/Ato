import os

import requests

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")


def build_messages(memory, user_input):
    messages = [
        {
            "role": "system",
            "content": (
                "You are Ato, a helpful AI assistant. "
                "Be natural, slightly conversational, and concise."
            )
        }
    ]

    # Add chat history (last 10 messages max)
    history = memory.get("chat_history", [])[-10:]

    for item in history:
        if "user" in item:
            messages.append({"role": "user", "content": item["user"]})
        if "ato" in item:
            messages.append({"role": "assistant", "content": item["ato"]})

    # current message
    messages.append({"role": "user", "content": user_input})

    return messages


def ask_deepseek(user_input, memory):
    if not DEEPSEEK_API_KEY:
        return "AI error: DEEPSEEK_API_KEY is not configured"

    url = "https://api.deepseek.com/chat/completions"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": build_messages(memory, user_input),
        "temperature": 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        data = response.json()

        return data["choices"][0]["message"]["content"]

    except Exception as e:
        return f"AI error: {str(e)}"
