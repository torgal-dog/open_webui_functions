"""
title: Advanced Prompt Injector
description: Filter that will detect and inject prompt configurations from user input using a <details> block.
id: system_prompt_injector
author: suurt8ll
author_url: https://github.com/suurt8ll
funding_url: https://github.com/suurt8ll/open_webui_functions
version: 0.6.0
"""

# The injection must follow this format (without triple quotes).
"""
<details type="prompt">
<summary>{{prompt_title}}</summary>
```json
{
    system_prompt: "{{system_prompt}}",
    temperature: {{temperature}}
}
```
</details>
{{content}}
"""

# IMPORTANT: Disable "Rich Text Input for Chat" in Open WebUI settings for this plugin to work correctly.
# TODO: Keep the latest prompt template settings if no setting were given in the last message.

from pydantic import BaseModel
from typing import Any, Awaitable, Callable, TYPE_CHECKING, cast
import json
import re

if TYPE_CHECKING:
    from utils.manifold_types import *  # My personal types in a separate file for more robustness.

# --- Constants ---
# Regex to find the entire details block and capture its components
# - Group 1: Full <details> block
# - Group 2: Content inside <summary> tag (prompt title)
# - Group 3: Content between </summary> and </details> (parameters)
DETAILS_BLOCK_REGEX = re.compile(
    r'(<details type="prompt">\s*<summary>(.*?)</summary>(.*?)^\s*</details>)',
    re.DOTALL | re.MULTILINE,
)


class Filter:
    class Valves(BaseModel):
        # Pass configuration options here if needed in the future
        # Example: default_system_prompt: str = "Default prompt"
        pass

    def __init__(self):
        # Valves are not used in this version but kept for potential future config
        self.valves = self.Valves()
        # Store the prompt title extracted during inlet for use in outlet
        self.prompt_title: str | None = None
        self.debug = True  # Enable or disable debug prints

    def inlet(self, body: "Body") -> "Body":
        """
        Processes incoming request body.
        Detects and extracts prompt injection details from all user messages.
        Applies extracted system prompt (if not empty) and temperature.
        Removes the injection block from all user messages.
        """
        if self.debug:
            print(f"\n--- Inlet Filter ---")
            print(f"Original Request Body:\n{json.dumps(body, indent=2)}")

        messages: list["Message"] = body.get("messages", [])
        if not messages:
            print(f"Warning: No messages found in the body.")
            return body

        latest_system_prompt: str | None = None
        latest_temperature: float | None = None
        self.prompt_title = None  # Reset stored title

        # Process all user messages to remove injections and track latest parameters
        for message in messages:
            if message.get("role") == "user":
                content = message.get("content", "")
                if not isinstance(content, str):
                    # FIXME: Handle non-string content (like images)
                    print(
                        f"Warning: User message content is not a string: {type(content)}. Skipping."
                    )
                    continue

                (
                    system_prompt,
                    temperature,
                    title,
                    modified_content,
                    injection_block,
                ) = self._extract_injection_params(content)

                # Update the message content to remove the injection
                message["content"] = modified_content

                # Track parameters if injection was found
                if title is not None:
                    latest_system_prompt = system_prompt
                    latest_temperature = temperature
                    self.prompt_title = title  # Keep the latest title

        # Apply the latest parameters if any were found
        if latest_system_prompt is not None:
            if latest_system_prompt:  # Check if it's a non-empty string
                self._apply_system_prompt(body, latest_system_prompt)
            else:
                if self.debug:
                    print("Extracted empty system prompt; will not set system prompt.")

        if latest_temperature is not None:
            self._update_options(body, "temperature", latest_temperature)
            if self.debug:
                print(f"Set temperature to: {latest_temperature}")

        if self.debug:
            print(
                f"\nModified Request Body (before sending to LLM):\n{json.dumps(body, indent=2)}"
            )

        return body

    async def outlet(
        self,
        body: dict[str, Any],
        __event_emitter__: Callable[["Event"], Awaitable[None]],
        __user__: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Processes the response body from the LLM.
        Adds the stored prompt title as a header to the latest assistant message.
        """
        if self.debug:
            print(f"\n--- Outlet Filter ---")
            print(f"Original Response Body:\n{json.dumps(body, indent=2)}")

        # Only add header if a prompt title was set during inlet
        if self.prompt_title:
            status_event: "StatusEvent" = {
                "type": "status",
                "data": {"description": self.prompt_title},
            }
            await __event_emitter__(status_event)
            # Reset prompt title after adding it to the response
            # self.prompt_title = None # Optional: Reset state if desired after use

        if self.debug:
            print(
                f"\nModified Response Body (after adding header):\n{json.dumps(body, indent=2)}"
            )

        return body

    """
    ---------- Helper methods inside the Pipe class ----------
    """

    def _update_options(self, body: "Body", key: str, value: Any):
        """Safely updates the 'options' dictionary in the request body."""
        if "options" not in body:
            body["options"] = {}
        body["options"][key] = value
        if self.debug:
            print(f"Updated options: set '{key}' to {value}")

    def _extract_injection_params(
        self, user_message_content: str
    ) -> tuple[str | None, float | None, str | None, str, str | None]:

        system_prompt = None
        temperature = None
        prompt_title = None
        modified_content = user_message_content
        injection_block = None

        match = DETAILS_BLOCK_REGEX.search(user_message_content)
        if match:
            injection_block = match.group(1)
            prompt_title = match.group(2).strip()
            params_block = match.group(3).strip()

            # Remove injection block from content
            modified_content = user_message_content.replace(injection_block, "").strip()

            # Extract JSON content from code block
            json_match = re.search(r"```json\s*(.*?)\s*```", params_block, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()
                try:
                    json_data = json.loads(json_str)
                except json.JSONDecodeError as e:
                    print(f"JSON Parse Error: {e}")
                    return (None, None, prompt_title, modified_content, injection_block)

                system_prompt = json_data.get("system_prompt")
                temp_value = json_data.get("temperature")
                if isinstance(temp_value, (int, float)):
                    temperature = float(temp_value)
                else:
                    print(f"Invalid temperature type: {type(temp_value)}")
            else:
                print("No JSON block found in parameters section")

        return (
            system_prompt,
            temperature,
            prompt_title,
            modified_content,
            injection_block,
        )

    def _apply_system_prompt(self, body: "Body", system_prompt_content: str):
        """
        Applies a *non-empty* system prompt to the request body.
        Updates the existing system message or inserts a new one at the beginning.
        Also updates the 'system' key in the 'options' dictionary if present.
        """
        # This function should only be called with non-empty system_prompt_content
        # due to the check in the inlet method.
        if not system_prompt_content:
            if self.debug:
                print(
                    "Internal Warning: _apply_system_prompt called with empty content. This should not happen."
                )
            return  # Do nothing if called with empty string despite the check

        messages: list["Message"] = body.get("messages", [])
        system_message_found = False

        # Iterate through messages to find and update the system message
        for message in messages:
            if message.get("role") == "system":
                message["content"] = system_prompt_content
                system_message_found = True
                if self.debug:
                    print(f"Updated existing system message.")
                break

        # If no system message exists, insert one at the beginning
        if not system_message_found:
            messages.insert(0, {"role": "system", "content": system_prompt_content})
            body["messages"] = messages  # Ensure the body reflects the change
            if self.debug:
                print(f"Inserted new system message at the beginning.")

        # Also update the 'system' option if it exists (some backends might use this)
        # We update this even if the message was pre-existing, to ensure consistency
        self._update_options(body, "system", system_prompt_content)
