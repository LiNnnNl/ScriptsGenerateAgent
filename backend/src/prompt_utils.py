"""Utilities for rendering prompt text kept in ``src.prompt_files``."""


def render_prompt(template: str, **values) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def prompt_lines(text: str) -> list[str]:
    return [line.strip() for line in text.strip().splitlines() if line.strip()]
