"""Minimal HTTPS client on the standard library (no requests/httpx on the boards).

Every call has a timeout. HTTP and network failures become ProviderError
with a kind; a failed attempt that may succeed again (connection reset,
5xx, a short rate limit) is retried once. The response body of an error is
kept short and never contains our request (keys are only in headers).
"""

from __future__ import annotations

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from typing import Iterator, Optional

from .base import (AUTH, BAD_RESPONSE, NETWORK, QUOTA, RATE_LIMIT, REQUEST, SERVER, TIMEOUT,
                   UNAVAILABLE, ProviderError)

log = logging.getLogger("mlearning.http")

USER_AGENT = "mLearning/0.1"
MAX_RETRY_AFTER = 5.0


def _error_from_http(provider: str, error: urllib.error.HTTPError) -> ProviderError:
    try:
        body = error.read(4000).decode("utf-8", "replace")
    except Exception:
        body = ""
    message = body
    code = ""
    try:
        parsed = json.loads(body)
        info = parsed.get("error", parsed) if isinstance(parsed, dict) else {}
        if isinstance(info, dict):
            message = str(info.get("message") or body)
            code = str(info.get("code") or info.get("type") or "")
    except ValueError:
        pass
    status = error.code
    message = message.strip().replace("\n", " ")[:300]
    if status in (401, 403):
        kind = AUTH
    elif status == 429:
        kind = QUOTA if ("quota" in code or "insufficient_quota" in message or "billing" in message.lower()) else RATE_LIMIT
    elif status == 404:
        kind = REQUEST
    elif 400 <= status < 500:
        kind = REQUEST
    else:
        kind = SERVER
    exc = ProviderError(kind, provider, f"HTTP {status}: {message}", status)
    retry_after = error.headers.get("retry-after") if error.headers else None
    try:
        exc.retry_after = float(retry_after) if retry_after else None
    except ValueError:
        exc.retry_after = None
    return exc


def _open(provider: str, request: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())
    except urllib.error.HTTPError as error:
        raise _error_from_http(provider, error) from None
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise ProviderError(TIMEOUT, provider, "no answer in time") from None
        if isinstance(reason, ssl.SSLError):
            raise ProviderError(NETWORK, provider, f"secure connection failed: {reason}") from None
        raise ProviderError(NETWORK, provider, str(reason)[:200]) from None
    except (socket.timeout, TimeoutError):
        raise ProviderError(TIMEOUT, provider, "no answer in time") from None
    except (ConnectionError, OSError) as error:
        raise ProviderError(NETWORK, provider, str(error)[:200]) from None


def _with_retry(provider: str, attempt, retries: int = 1):
    for number in range(retries + 1):
        try:
            return attempt()
        except ProviderError as exc:
            last = number == retries
            # A timeout is not retried: the learner has already waited the whole timeout.
            if last or exc.kind not in (NETWORK, SERVER, RATE_LIMIT):
                raise
            wait = 0.5
            if exc.kind == RATE_LIMIT:
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None or retry_after > MAX_RETRY_AFTER:
                    raise
                wait = retry_after
            log.info("%s: %s, retrying once", provider, exc.kind)
            time.sleep(wait)               # back-off before a retry, not a race fix


def _request(url: str, data: bytes, headers: dict) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT}
    merged.update(headers)
    return urllib.request.Request(url, data=data, headers=merged, method="POST")


def post_json(provider: str, url: str, body: dict, headers: dict, timeout: float,
              retries: int = 1) -> dict:
    data = json.dumps(body).encode("utf-8")
    headers = dict(headers, **{"Content-Type": "application/json"})

    def attempt():
        with _open(provider, _request(url, data, headers), timeout) as response:
            raw = response.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ProviderError(BAD_RESPONSE, provider, "answer was not JSON") from None

    return _with_retry(provider, attempt, retries)


def multipart(fields: dict, files: dict) -> tuple:
    """(body, content type) for ``fields`` {name: str} and ``files`` {name: (filename, bytes, type)}."""
    boundary = "mlearning-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode("utf-8"))
    for name, (filename, content, ctype) in files.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                     f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode("utf-8"))
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post_multipart(provider: str, url: str, fields: dict, files: dict, headers: dict,
                   timeout: float, retries: int = 1) -> dict:
    data, ctype = multipart(fields, files)
    headers = dict(headers, **{"Content-Type": ctype})

    def attempt():
        with _open(provider, _request(url, data, headers), timeout) as response:
            raw = response.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ProviderError(BAD_RESPONSE, provider, "answer was not JSON") from None

    return _with_retry(provider, attempt, retries)


def post_stream(provider: str, url: str, body: dict, headers: dict, timeout: float,
                chunk: int = 4800, cancelled=None) -> Iterator[bytes]:
    """POST JSON and yield the response body in chunks as it arrives.

    The connection is opened (and its errors raised) on the first ``next``.
    ``cancelled()`` returning True stops reading and closes the connection.
    """
    data = json.dumps(body).encode("utf-8")
    headers = dict(headers, **{"Content-Type": "application/json"})
    response = _with_retry(provider, lambda: _open(provider, _request(url, data, headers), timeout))
    try:
        while True:
            if cancelled is not None and cancelled():
                return
            try:
                piece = response.read(chunk)
            except (socket.timeout, TimeoutError):
                raise ProviderError(TIMEOUT, provider, "audio stopped arriving") from None
            except OSError as error:
                raise ProviderError(NETWORK, provider, str(error)[:200]) from None
            if not piece:
                return
            yield piece
    finally:
        response.close()


def reachable(host: str, port: int = 443, timeout: float = 3.0) -> Optional[str]:
    """None if a TCP connection to host:port opens, else why not."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as error:
        return str(error)[:120] or UNAVAILABLE
