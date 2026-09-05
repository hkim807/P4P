"""Command-line client that exercises the LLM gateway from any network computer."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def send_message(server_url: str, message: str) -> dict:
    request = Request(
        f"{server_url.rstrip('/')}/chat",
        data=json.dumps({"message": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=35) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one input to the LLM gateway.")
    parser.add_argument("message", help="Text to send to the model")
    parser.add_argument(
        "--server",
        default=os.getenv("LLM_GATEWAY_URL", "http://127.0.0.1:6000"),
        help="Gateway base URL",
    )
    args = parser.parse_args()

    try:
        result = send_message(args.server, args.message)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(f"Gateway returned HTTP {error.code}: {detail}", file=sys.stderr)
        return 1
    except URLError as error:
        print(f"Could not reach the gateway: {error.reason}", file=sys.stderr)
        return 1

    print(result["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
