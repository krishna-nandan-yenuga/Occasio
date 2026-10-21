"""Input hygiene for text that reaches an LLM prompt.

User text is cleaned (normalised, control characters removed, length capped) and wrapped in
<user_input> delimiters that the system prompt declares to be data. Likely prompt-injection
phrasing is logged rather than blocked: false positives on real event descriptions would cost
more than the attempts, which the delimiters and system prompt already defuse.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 2000
# Unicode categories removed from user text: control, format (zero-width, bidi), surrogate.
_STRIPPED_CATEGORIES = {"Cc", "Cf", "Cs"}
_KEPT_CONTROLS = {"\n", "\t"}
_DELIMITER = re.compile(r"<\s*/?\s*user_input\b[^>]*>", re.IGNORECASE)
_INJECTION_PATTERNS = {
    "override": r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules|prompt)\b",
    "role_change": r"\b(you are now|act as|pretend to be|from now on you)\b",
    "prompt_leak": r"\b(reveal|show|print|repeat)\b.{0,40}\b(system prompt|instructions)\b",
    "fake_markup": r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    "jailbreak": r"\b(jailbreak|developer mode|dan mode)\b",
}
_INJECTION = {
    name: re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for name, pattern in _INJECTION_PATTERNS.items()
}


def clean_text(text: str, max_len: int | None = MAX_TEXT_LENGTH) -> str:
    """Normalise `text`, strip control and invisible characters and delimiter tags, cap length.

    Newlines and tabs are kept. `max_len=None` skips truncation, for structured data blocks
    that must not be cut mid-way.
    """
    text = unicodedata.normalize("NFKC", text.replace("\r\n", "\n"))
    text = "".join(
        ch
        for ch in text
        if ch in _KEPT_CONTROLS or unicodedata.category(ch) not in _STRIPPED_CATEGORIES
    )
    # Repeat until stable so nested fragments cannot reassemble a delimiter.
    while (stripped := _DELIMITER.sub("", text)) != text:
        text = stripped
    text = text.strip()
    return text if max_len is None else text[:max_len].rstrip()


def injection_signals(text: str) -> list[str]:
    """Names of the injection heuristics that `text` matches (empty if none)."""
    return [name for name, pattern in _INJECTION.items() if pattern.search(text)]


def wrap_user_input(text: str, max_len: int | None = MAX_TEXT_LENGTH) -> str:
    """Clean `text`, log any injection signals, and wrap it in <user_input> delimiters."""
    cleaned = clean_text(text, max_len)
    signals = injection_signals(cleaned)
    if signals:
        logger.warning("Possible prompt injection in user input: %s", ", ".join(signals))
    return f"<user_input>\n{cleaned}\n</user_input>"
