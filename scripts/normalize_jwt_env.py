import re
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

def normalize_block(text: str, var: str) -> str:
    # Match triple-quoted block: VAR="""..."""
    pattern_triple = re.compile(rf"^{var}=\"\"\"(.*?)\"\"\"\s*$", re.S | re.M)
    m = pattern_triple.search(text)
    if m:
        content = m.group(1)
        # Normalize newlines and trim
        content = content.replace("\r\n", "\n").replace("\r", "\n").strip("\n ")
        # Escape newlines as \n literal
        escaped = content.replace("\n", r"\n")
        repl = f"{var}={escaped}"
        return pattern_triple.sub(repl, text)
    # If already single-line with \n, leave as is
    return text


def main():
    if not ENV_PATH.exists():
        raise SystemExit(f".env not found at {ENV_PATH}")
    original = ENV_PATH.read_text(encoding="utf-8")
    updated = normalize_block(original, "JWT_PRIVATE_KEY")
    updated = normalize_block(updated, "JWT_PUBLIC_KEY")
    if updated != original:
        ENV_PATH.write_text(updated, encoding="utf-8")

if __name__ == "__main__":
    main()
