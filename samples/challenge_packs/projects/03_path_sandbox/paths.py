"""Join user-supplied relative paths safely."""

import os


def safe_join(base: str, user_path: str) -> str:
    # BUG: only strips single ".."
    cleaned = user_path.replace("..", "")
    return os.path.join(base, cleaned)


def open_user_file(base: str, user_path: str):
    path = safe_join(base, user_path)
  # BUG: no check that resolved path is under base
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def is_valid_segment(name: str) -> bool:
    if not name:
        return False
    # BUG: allows leading slash
    return "/" not in name or name.startswith("/")
