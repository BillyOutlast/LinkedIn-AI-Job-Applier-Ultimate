"""Taleo authenticator skeleton — full implementation lands in Task 2."""


class TaleoAuthenticator:
    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password

    def has_credentials(self):
        return bool(self.username and self.password)

    async def authenticate(self, page):
        raise RuntimeError("TaleoAuthenticator not yet implemented")
