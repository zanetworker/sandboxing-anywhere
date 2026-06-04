from openai import OpenAI
import os, json

url = os.environ.get("VLLM_BASE_URL", "https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1")
model = os.environ.get("VLLM_MODEL", "qwen3-8b-fp8")

client = OpenAI(base_url=url, api_key="unused")

models = client.models.list()
print(f"Connected to vLLM: {models.data[0].id}")

resp = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": "You are a helpful assistant. Be very brief."},
        {"role": "user", "content": "In one sentence, what is the capital of France?"},
    ],
    max_tokens=50,
    temperature=0.1,
)
print(f"Model response: {resp.choices[0].message.content}")
