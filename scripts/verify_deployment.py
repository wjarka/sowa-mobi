"""Wait for the expected release, not merely a healthy previous container."""

import json
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main() -> None:
    url, revision = sys.argv[1:]
    deadline = time.monotonic() + 600
    last_observation = "No response"
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=10) as response:
                health = json.load(response)
            if health.get("status") == "ok" and health.get("revision") == revision:
                print(f"Deployment healthy: {revision}", flush=True)
                return
            last_observation = f"status={health.get('status')}, revision={health.get('revision')}"
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            last_observation = str(exc)
        print(f"Waiting for {revision[:12]}: {last_observation}", flush=True)
        time.sleep(10)
    raise SystemExit(f"Deployment did not become healthy within 10 minutes: {last_observation}")


if __name__ == "__main__":
    main()
