# opencode (v2) → karspark

Tested 2026-10-22 with opencode v2.0.32: `opencode run --model karspark/qwen3.8-flash` works through the gateway.

Add to `~/.config/opencode/opencode.jsonc` under `"providers"` (the key comes from the env var, never the file):

```json
"karspark": {
  "name": "karspark (DGX Spark)",
  "env": ["KARSPARK_API_KEY"],
  "package": "@opencode/ai/providers/openai-compatible",
  "settings": { "baseURL": "http://100.101.249.74:8080/v1" },
  "models": {
    "qwen3.8-flash": {
      "name": "Qwen3.8 Flash-Next (Spark)",
      "limit": { "context": 262144, "output": 32768 },
      "capabilities": {
        "tools": true, "input": ["text"], "output": ["text"], "temperature": true,
        "reasoning": { "supported": true, "toggle": true, "effort": ["low", "medium", "xhigh"] }
      },
      "compatibility": { "reasoningField": "reasoning", "maxTokensField": "max_tokens", "streamUsage": true },
      "variants": [
        { "id": "fast",         "body": { "chat_template_kwargs": { "enable_thinking": false } } },
        { "id": "think-low",    "body": { "reasoning_effort": "low" } },
        { "id": "think-medium", "body": { "reasoning_effort": "medium" } },
        { "id": "think-xhigh",  "body": { "reasoning_effort": "xhigh" } }
      ]
    }
  }
}
```

and `"model": "karspark/qwen3.8-flash"` at the top level to make it the default.

Notes:
- `reasoningField: "reasoning"`: vLLM streams thinking as `delta.reasoning`.
- Reasoning: thinking is **off** unless a request sends `reasoning_effort` (`low` | `medium` | `xhigh`, which
  turns thinking on) or `chat_template_kwargs.enable_thinking: true` (defaults to `xhigh`). Anything else is
  rejected by the model's template. Switch variants in opencode with the `variant_cycle` keybind.
- The background service reads `KARSPARK_API_KEY` from the environment it was started in, so run
  `opencode service restart` from a shell that has the variable.
