"""Synthetic HTTP fixtures used only by isolated tests, never by the server."""


def search_payload() -> dict:
    return {
        "products": [{
            "id": 1,
            "title": "Test fixture product",
            "price": 19.99,
            "description": "Synthetic fixture for isolated tests.",
            "category": "test-category",
            "stock": 3,
            "rating": 4.5,
            "discountPercentage": 2.5,
            "availabilityStatus": "In Stock",
            "images": ["https://example.test/product.webp"],
            "reviews": [{"reviewerEmail": "private@example.test"}],
        }],
        "total": 1,
        "skip": 0,
        "limit": 1,
    }
