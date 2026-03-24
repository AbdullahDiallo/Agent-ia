from __future__ import annotations

import re
from typing import Literal

SanitizeMode = Literal["strict", "normal"]

# Regex basiques pour PII
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}")


def sanitize_for_llm(text: str, mode: SanitizeMode = "normal") -> str:
  """Masque les données sensibles avant envoi au LLM.

  - Remplace emails, numéros de téléphone et UUID par des tokens
  - En mode "strict", masque aussi des suites de chiffres longues (potentiels IDs internes)
  """
  if not text:
    return text

  out = text
  out = EMAIL_RE.sub("[EMAIL]", out)
  out = PHONE_RE.sub("[PHONE]", out)
  out = UUID_RE.sub("[UUID]", out)

  if mode == "strict":
    # masque les longues suites de chiffres (>= 8) qui peuvent être des IDs externes
    out = re.sub(r"\b\d{8,}\b", "[ID]", out)

  return out
