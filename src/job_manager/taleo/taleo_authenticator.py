"""Taleo login flow."""

from __future__ import annotations

from typing import Any


class TaleoAuthenticator:
    def __init__(self, username: str = None, password: str = None):
        self.username = username
        self.password = password

    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    async def authenticate(self, page: Any) -> None:
        if not self.has_credentials():
            raise RuntimeError("No credentials configured")
        await page.locator(
            "input[type='text']:first-of-type, input[name='username'], input#username"
        ).first.fill(self.username)
        await page.locator("input[type='password']").first.fill(self.password)
        await page.locator(
            "button:has-text('Sign In'), button:has-text('Log In'), input[type='submit']"
        ).first.click()
        await page.wait_for_load_state("networkidle", timeout=10000)
