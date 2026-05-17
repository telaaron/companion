"""Echo skill — used in tests."""

import json
import sys


def main() -> None:
    args_json = sys.argv[1] if len(sys.argv) > 1 else "{}"
    try:
        args = json.loads(args_json)
    except Exception:
        args = {}
    text = args.get("text", "")
    print(text, end="")


if __name__ == "__main__":
    main()
