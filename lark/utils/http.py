import logging
import os

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lark import __version__

log = logging.getLogger(__name__)


class HttpError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def user_agent() -> str:
    mailto = os.getenv("LARK_CONTACT_EMAIL", "")
    return f"lark/{__version__} (+https://github.com/iamsikun/lark{'; mailto:' + mailto if mailto else ''})"


def _session_headers(headers: dict | None) -> dict:
    return {"User-Agent": user_agent(), **(headers or {})}


def _limit(url: str, limiter) -> None:
    if limiter is not None:
        limiter.wait(url)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), retry=retry_if_exception_type(HttpError))
def get(url: str, headers=None, params=None, timeout=30, limiter=None) -> requests.Response:
    _limit(url, limiter)
    r = requests.get(url, headers=_session_headers(headers), params=params or {}, timeout=timeout)
    if r.status_code == 429 or r.status_code >= 500:
        log.warning("GET %s -> %s (retrying)", url, r.status_code)
        raise HttpError(r.status_code, r.text[:200])
    if r.status_code >= 400:
        raise HttpError(r.status_code, r.text[:200])
    return r


def get_json(url: str, headers=None, params=None, timeout=30, limiter=None):
    r = get(url, headers=headers, params=params, timeout=timeout, limiter=limiter)
    if "json" in r.headers.get("content-type", ""):
        return r.json()
    return r.text


def get_text(url: str, headers=None, params=None, timeout=30, limiter=None) -> str:
    return get(url, headers=headers, params=params, timeout=timeout, limiter=limiter).text


def post_json(url: str, payload: dict, headers=None, timeout=30):
    r = requests.post(url, json=payload, headers=_session_headers(headers), timeout=timeout)
    if r.status_code >= 400:
        raise HttpError(r.status_code, r.text[:200])
    if "json" in r.headers.get("content-type", ""):
        return r.json()
    return r.text
