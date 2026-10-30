"""Environment configuration for the product API client."""

import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    product_api_timeout_seconds: float

    @classmethod
    def from_environment(cls) -> "Settings":
        try:
            timeout = float(os.getenv("PRODUCT_API_TIMEOUT_SECONDS", "10"))
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
        except ValueError:
            raise RuntimeError(
                "PRODUCT_API_TIMEOUT_SECONDS must be a finite positive number."
            ) from None
        return cls(product_api_timeout_seconds=timeout)
