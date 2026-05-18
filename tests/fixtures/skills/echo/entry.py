"""Echo skill — reads JSON args from stdin and prints the 'text' field."""

import json
import sys


def main() -> None:
    raw = sys.stdin.read()
    try:
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}
    text = args.get("text", "")
    print(text, end="")


if __name__ == "__main__":
    main()
