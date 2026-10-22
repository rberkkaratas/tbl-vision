"""TBF fetcher: a real Chrome session (SeleniumBase CDP mode) running fetch() in-page.

tbf.org.tr sits behind Cloudflare, so plain HTTP clients get 403. A browser that has
loaded a TBF page holds the session cookies and its same-origin fetch() calls succeed.
We never solve captchas: on a challenge we wait, reload the landing page and retry; if
challenges persist we raise ChallengeBlocked so the sync stops and can resume later.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

import structlog

from .base import ChallengeBlocked, FetchResult, Resource

log = structlog.get_logger()

_FETCH_JS = """(async () => {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 30000);
  try {
    const r = await fetch(%s, {method: %s, credentials: 'include', signal: ctl.signal,
                               headers: {'Accept': 'application/json'}});
    const h = {}; r.headers.forEach((v, k) => h[k] = v);
    return JSON.stringify({status: r.status, headers: h, text: await r.text()});
  } catch (e) { return JSON.stringify({status: 0, headers: {}, text: '', error: String(e)}); }
  finally { clearTimeout(t); }
})()"""


# Title is localised ("Just a moment..." / "Bir dakika lütfen..."), so also look for CF markup.
_PAGE_CHALLENGE_JS = """(() => {
  const t = (document.title || '').toLowerCase();
  if (t.includes('just a moment') || t.includes('bir dakika')) return true;
  return !!document.querySelector('#challenge-form, #challenge-running, #challenge-stage, script[src*="/cdn-cgi/challenge-platform/"]');
})()"""


def is_challenge(status: int, headers: dict[str, str], text: str) -> bool:
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    head = text[:2000].lstrip().lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return True  # the API only ever returns JSON
    return status in (403, 503) and "cloudflare" in head


class BrowserFetcher:
    source = "tbf"

    def __init__(
        self,
        landing_url: str,
        profile_dir: Path,
        headless: bool = False,
        delay: tuple[float, float] = (2.0, 4.0),
        max_challenge_retries: int = 3,
        auto_wait: float = 45.0,
        human_wait: float = 180.0,
        eval_timeout: float = 45.0,
    ):
        self.eval_timeout = eval_timeout
        self.auto_wait = auto_wait
        self.human_wait = human_wait
        self.landing_url = landing_url
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.delay = delay
        self.max_challenge_retries = max_challenge_retries
        self.challenges = 0
        self._cm = None
        self._sb = None
        self._last = 0.0

    # -- session -----------------------------------------------------------------
    def _start(self) -> None:
        from seleniumbase import SB  # heavy import, only when the browser is needed

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._cm = SB(uc=True, locale="tr", headless=self.headless, user_data_dir=str(self.profile_dir))
        self._sb = self._cm.__enter__()
        self._sb.activate_cdp_mode(self.landing_url)
        if not self._warm(first=True):
            raise ChallengeBlocked("Cloudflare challenge did not clear on the landing page")

    def _page_challenged(self) -> bool:
        try:
            return bool(self._sb.cdp.evaluate(_PAGE_CHALLENGE_JS))
        except Exception:  # page mid-navigation
            return True

    def _wait_clear(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(2)
            if not self._page_challenged():
                return True
        return False

    def _warm(self, first: bool = False) -> bool:
        if not first:
            self._sb.cdp.open(self.landing_url)
        time.sleep(3)
        # Cloudflare's non-interactive check usually clears by itself in a real browser.
        ok = self._wait_clear(self.auto_wait)
        if not ok and not self.headless and self.human_wait > 0:
            # We never click the captcha ourselves; a person at the keyboard may.
            log.warning("tbf.needs_human", hint="Chrome penceresindeki Cloudflare kutusunu elle işaretleyin",
                        wait_s=self.human_wait)
            ok = self._wait_clear(self.human_wait)
        if ok:
            log.info("tbf.session_ready", title=self._sb.cdp.get_title())
        else:
            log.warning("tbf.session_still_challenged", title=self._sb.cdp.get_title())
        return ok

    def _eval(self, res: Resource) -> dict:
        js = _FETCH_JS % (json.dumps(res.url), json.dumps(res.method))
        cdp = self._sb.cdp
        # If the page navigates (e.g. into a challenge) mid-await, the promise never settles.
        coro = asyncio.wait_for(cdp.page.evaluate(js, await_promise=True), timeout=self.eval_timeout)
        try:
            raw = cdp.loop.run_until_complete(coro)
        except TimeoutError:
            log.warning("tbf.eval_timeout", url=res.url, timeout_s=self.eval_timeout)
            return {"status": 0, "headers": {}, "text": "", "stalled": True}
        return json.loads(raw)

    def _pace(self) -> None:
        wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    # -- Fetcher -----------------------------------------------------------------
    def fetch(self, res: Resource, etag: str | None = None) -> FetchResult:
        if self._sb is None:
            self._start()
        for attempt in range(self.max_challenge_retries + 1):
            self._pace()
            t0 = time.monotonic()
            d = self._eval(res)
            ms = int((time.monotonic() - t0) * 1000)
            text, headers = d.get("text", ""), d.get("headers", {})
            if d.get("error"):
                return FetchResult(status=0, elapsed_ms=ms, error=d["error"])
            if not d.get("stalled") and not is_challenge(d["status"], headers, text):
                return FetchResult(status=d["status"], body=text.encode(), headers=headers, elapsed_ms=ms)
            self.challenges += 1
            backoff = 30 * (attempt + 1) ** 2
            log.warning("tbf.challenge", url=res.url, attempt=attempt + 1, backoff_s=backoff, total=self.challenges)
            if attempt == self.max_challenge_retries:
                break
            time.sleep(backoff)
            self._warm()
        raise ChallengeBlocked(f"Cloudflare challenge persisted for {res.url}")

    def close(self) -> None:
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
            self._cm = self._sb = None
