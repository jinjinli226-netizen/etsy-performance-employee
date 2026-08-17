from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from app.training.etsy import extract_listing_urls


class BrowserFetchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)


def resolve_visible_browser() -> Path:
    for candidate in BROWSER_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise BrowserFetchError("browser_unavailable", "No supported visible browser is installed.")


def _looks_real(url: str, html: str) -> bool:
    sample = re.sub(r"<[^>]+>", " ", html[:20_000])
    lowered = sample.casefold()
    if any(
        marker in lowered
        for marker in (
            "captcha",
            "verify you are human",
            "access denied",
            "attention required",
            "data dome",
        )
    ):
        return False
    if "/shop/" in url:
        return bool(extract_listing_urls(html))
    return '"@type":"Product"' in html.replace(" ", "") or len(sample) > 800


class VisibleEtsyBrowser:
    """Visible, persistent Etsy browser. It does not solve or bypass challenges."""

    def __init__(
        self,
        data_root: Path,
        *,
        wait_seconds: float = 90,
        sleep: Callable[[float], None] = time.sleep,
        browser_path: Path | None = None,
    ) -> None:
        if not 15 <= wait_seconds <= 300:
            raise ValueError("browser wait must be between 15 and 300 seconds")
        root = data_root.resolve()
        profile = (root / "browser-profile" / "shared").resolve()
        if not profile.is_relative_to(root):
            raise ValueError("browser profile path is invalid")
        profile.mkdir(parents=True, exist_ok=True)
        executable = (browser_path or resolve_visible_browser()).resolve(strict=True)
        from DrissionPage import Chromium, ChromiumOptions

        options = ChromiumOptions()
        options.set_browser_path(str(executable))
        options.set_user_data_path(str(profile))
        options.set_argument("--window-size=1366,900")
        options.headless(False)
        self._browser = Chromium(options)
        self._tab = self._browser.latest_tab
        self._wait_seconds = wait_seconds
        self._sleep = sleep
        self._warmed = False

    def _warm(self) -> None:
        if self._warmed:
            return
        self._warmed = True
        try:
            self._tab.get("https://www.etsy.com/")
            self._tab.wait.doc_loaded(timeout=30)
            self._sleep(3)
        except Exception:
            return

    def fetch(self, url: str) -> str:
        self._warm()
        deadline = time.monotonic() + self._wait_seconds
        while time.monotonic() < deadline:
            try:
                self._tab.get(url)
                self._tab.wait.doc_loaded(timeout=30)
                self._sleep(5)
                html = self._tab.html or ""
            except Exception:
                self._sleep(3)
                continue
            if _looks_real(url, html):
                return html
            self._sleep(6)
        raise BrowserFetchError("etsy_blocked_or_empty", "Etsy did not return usable page content.")

    def close(self) -> None:
        try:
            self._browser.quit()
        except Exception:
            return

    def __enter__(self) -> "VisibleEtsyBrowser":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
