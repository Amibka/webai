import requests


class LMStudioClient:
    def __init__(self, host, port, timeout=120):
        self.base_url = f"http://{host}:{port}/v1/chat/completions"
        self.timeout = timeout

    def complete_chat(self, messages, temperature=0.7, max_tokens=-1):
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = requests.post(self.base_url, json=payload, timeout=self.timeout)
        response.raise_for_status()

        data = response.json()
        message = data["choices"][0]["message"]

        return {
            "content": message.get("content") or "",
            "reasoning": message.get("reasoning_content"),
        }

