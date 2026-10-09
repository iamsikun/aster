import re

_AUTH = re.compile(r"\b401\b|\b403\b|unauthori[sz]ed|not logged in|log ?in|authenticat|oauth|invalid api key|token (has )?expired|credential", re.I)
_RATE = re.compile(r"\b429\b|rate.?limit|usage limit|limit reached|quota|overloaded|too many requests|\b529\b", re.I)
_UNAVAILABLE = re.compile(r"not supported|model .*not found|unknown model|does not exist|not available|no such model", re.I)


def classify_error(text: str) -> str:
    """Map provider error text to an error_kind: auth | rate_limit | unavailable | other."""
    t = text or ""
    if _RATE.search(t):
        return "rate_limit"
    if _UNAVAILABLE.search(t):
        return "unavailable"
    if _AUTH.search(t):
        return "auth"
    return "other"
