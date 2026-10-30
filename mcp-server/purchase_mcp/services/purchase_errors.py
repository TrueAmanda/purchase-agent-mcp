"""Sanitized business and persistence error messages for MCP callers."""


class PurchaseRequestError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")
