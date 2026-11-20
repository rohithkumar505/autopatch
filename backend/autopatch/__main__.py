"""Allow ``python -m autopatch`` (same as the ``autopatch`` console script)."""

from autopatch.main import run

if __name__ == "__main__":
    run()