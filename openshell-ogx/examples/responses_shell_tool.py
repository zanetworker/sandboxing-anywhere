"""
Responses API with shell tool executing in an OpenShell sandbox.

Sends a Responses API request with a shell tool. The model proposes
commands, OGX executes them in the OpenShell sandbox, and feeds the
output back to the model for a final answer.

Requires:
  - A running OpenShell gateway
  - OGX server with remote::openshell provider and inference configured
  - OPENAI_API_KEY (or another configured inference provider)
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI


def main() -> None:
    base_url = os.environ.get("OGX_BASE_URL", "http://localhost:8321/v1")
    model = os.environ.get("OGX_MODEL", "openai/gpt-4o-mini")

    client = OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "unused"))

    print("=" * 60)
    print("Responses API + Shell Tool + OpenShell Sandbox")
    print("=" * 60)
    print(f"  Server: {base_url}")
    print(f"  Model:  {model}")
    print()

    response = client.responses.create(
        model=model,
        tools=[{"type": "shell", "environment": {"type": "container_auto"}}],
        input="Write a Python script that generates the first 20 prime numbers, run it, and show the output.",
    )

    print("Response output items:")
    print()
    for item in response.output:
        if item.type == "shell_call":
            print(f"  [shell_call] action={item.action}")
        elif item.type == "shell_call_output":
            print(f"  [shell_call_output] exit_code={item.exit_code}")
            if item.stdout:
                print(f"    stdout: {item.stdout[:200]}")
        elif item.type == "message":
            for content in item.content:
                if hasattr(content, "text"):
                    print(f"  [message] {content.text[:500]}")
        else:
            print(f"  [{item.type}]")

    print()
    print("Final output:")
    print(response.output_text)

    print()
    print("RESPONSES API TEST PASSED")


if __name__ == "__main__":
    main()
