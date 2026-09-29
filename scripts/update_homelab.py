"""Update only the sowa-mobi image pin in a disposable homelab checkout."""

import re
import sys
from pathlib import Path


def update_compose(contents: str, digest: str, revision: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Expected a SHA-256 image digest")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full Git commit SHA")
    replacement = (
        f"  sowa-mobi:\n"
        f"    # Source: https://github.com/wjarka/sowa-mobi/commit/{revision}\n"
        f"    image: ghcr.io/wjarka/sowa-mobi@{digest}\n"
    )
    # The build form supports the one-time migration from server-side builds.
    pattern = (
        r"(?m)^  sowa-mobi:\n"
        r"(?:    # Source: https://github\.com/wjarka/sowa-mobi/commit/[0-9a-f]{40}\n)?"
        r"(?:    image: ghcr\.io/wjarka/sowa-mobi@sha256:[0-9a-f]{64}\n"
        r'|    build:\n      context: "https://github\.com/wjarka/sowa-mobi\.git#[0-9a-f]{40}"\n'
        r"      dockerfile: Dockerfile\n)"
    )
    updated, count = re.subn(pattern, replacement, contents)
    if count != 1:
        raise ValueError("Expected exactly one recognized sowa-mobi image or build definition")
    return updated


def main() -> None:
    checkout, digest, revision = sys.argv[1:]
    path = Path(checkout) / "stacks/sowa-mobi/compose.yaml"
    path.write_text(update_compose(path.read_text(), digest, revision))


if __name__ == "__main__":
    main()
