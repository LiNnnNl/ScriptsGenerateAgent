import json
import os
import time
import urllib.error
import urllib.request


class LLMJsonClient:
    DEFAULT_TIMEOUT = 120
    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_RETRIES = 3

    def __init__(self):
        self.api_key = os.getenv("API_KEY", "").strip()
        raw_base = os.getenv("BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
        self.api_url = raw_base + "/chat/completions"
        self.model = os.getenv("CINEMATOGRAPHY_MODEL") or os.getenv("MODEL", "deepseek-chat")
        self.model = self.model.strip()
        self.timeout = self.DEFAULT_TIMEOUT
        self.max_tokens = self.DEFAULT_MAX_TOKENS
        self.retries = self.DEFAULT_RETRIES

    @property
    def enabled(self):
        return bool(self.api_key)

    def complete_json(self, system_prompt, user_payload):
        if not self.enabled:
            raise RuntimeError("API_KEY not set in .env")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
            ],
            "temperature": 0,
            "top_p": 1,
            "stream": False,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(
                    self.api_url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.api_key,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))

                choice = response_payload["choices"][0]
                finish_reason = choice.get("finish_reason")
                message = choice.get("message", {})
                content = message.get("content")
                if not content or not str(content).strip():
                    raise RuntimeError("LLM returned empty content.")
                if finish_reason == "length":
                    raise RuntimeError("LLM response was truncated (finish_reason=length).")
                return self._parse_json_object(content)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 4))

        raise RuntimeError(f"LLM API call failed after {self.retries} attempts: {last_error}")

    def _parse_json_object(self, content):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM response did not contain a valid JSON object.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("LLM response must be a JSON object.")
        return parsed
