name: echo
description: Echo back the text argument as-is. Used in tests.
entry: skill.py
args_schema: {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
