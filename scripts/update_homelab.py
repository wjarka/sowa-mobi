"""Update only the sowa-mobi image pin in a disposable homelab checkout."""

import os
import re
import sys
from pathlib import Path


def update_compose(contents: str, digest: str, revision: str, *,
                   image: str = "ghcr.io/wjarka/sowa-mobi",
                   source: str = "wjarka/sowa-mobi") -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", image) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        raise ValueError("Invalid image or source repository")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Expected a SHA-256 image digest")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full Git commit SHA")
    replacement = (
        f"  sowa-mobi:\n"
        f"    # Source: https://github.com/{source}/commit/{revision}\n"
        f"    image: {image}@{digest}\n"
    )
    # The build form supports the one-time migration from server-side builds.
    pattern = (
        r"(?m)^  sowa-mobi:\n"
        rf"(?:    # Source: https://github\.com/{re.escape(source)}/commit/[0-9a-f]{{40}}\n)?"
        rf"(?:    image: {re.escape(image)}@sha256:[0-9a-f]{{64}}\n"
        rf'|    build:\n      context: "https://github\.com/{re.escape(source)}\.git#[0-9a-f]{{40}}"\n'
        r"      dockerfile: Dockerfile\n)"
    )
    updated, count = re.subn(pattern, replacement, contents)
    if count != 1:
        raise ValueError("Expected exactly one recognized sowa-mobi image or build definition")
    return updated


def main() -> None:
    checkout, digest, revision = sys.argv[1:]
    relative = Path(os.environ.get("DEPLOY_COMPOSE_PATH", "stacks/sowa-mobi/compose.yaml"))
    path = (Path(checkout) / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(Path(checkout).resolve()):
        raise ValueError("Compose path must stay inside the deployment checkout")
    source = os.environ.get("SOURCE_REPOSITORY", "wjarka/sowa-mobi")
    image = os.environ.get("IMAGE_REPOSITORY", f"ghcr.io/{source.lower()}")
    path.write_text(update_compose(path.read_text(), digest, revision, image=image, source=source))


if __name__ == "__main__":
    main()
