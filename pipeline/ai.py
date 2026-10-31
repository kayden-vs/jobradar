"""One bounded retry policy and circuit breaker for every Gemini caller."""
import logging
import os
import random
import time
import httpx
from google import genai
from google.genai import types
from pipeline.gemini_throttle import gemini_throttle

logger = logging.getLogger(__name__)
MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite')
_failures = 0
_open_until = 0.0


class ProviderUnavailable(RuntimeError):
    pass


def client() -> genai.Client:
    key = os.getenv('GEMINI_API_KEY')
    if not key:
        raise ProviderUnavailable('GEMINI_API_KEY is missing')
    # Disable nested SDK retries: each actual attempt must pass our shared throttle.
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=45000, retry_options=types.HttpRetryOptions(attempts=1)))


def generate(client, **kwargs):
    """Retry transient responses only; open circuit after three failed requests."""
    global _failures, _open_until
    if time.monotonic() < _open_until:
        raise ProviderUnavailable('Gemini temporarily unavailable; work deferred')
    for attempt in range(3):
        gemini_throttle()
        try:
            response = client.models.generate_content(**kwargs)
            _failures = 0
            return response
        except Exception as exc:
            code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
            transient = code in (408, 429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError))
            if not transient:
                raise
            if attempt < 2:
                delay = 5 * 2**attempt + random.uniform(0, 1)
                logger.warning('Gemini HTTP %s; retry %d/2 in %.1fs', code, attempt + 1, delay)
                time.sleep(delay)
                continue
            _failures += 1
            if _failures >= 3:
                _open_until = time.monotonic() + 300
            raise ProviderUnavailable(f'Gemini HTTP {code}; retry on a later run') from exc
