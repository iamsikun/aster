import requests, logging
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger(__name__)

class HttpError(Exception): pass

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), retry=retry_if_exception_type(HttpError))
def get_json(url: str, headers=None, params=None, timeout=30):
    r = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
    if r.status_code >= 400:
        log.warning("GET %s -> %s: %s", url, r.status_code, r.text[:200])
        raise HttpError(f"HTTP {r.status_code}")
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        return r.json()
    return r.text

def post_json(url: str, payload: dict, headers=None, timeout=30):
    r = requests.post(url, json=payload, headers=headers or {}, timeout=timeout)
    if r.status_code >= 400:
        raise HttpError(f"HTTP {r.status_code}: {r.text[:200]}")
    if "json" in r.headers.get("content-type", ""):
        return r.json()
    return r.text
