"""Validated product data and structured MCP search results."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Product(BaseModel):
    # Advertise normalized output names, while accepting DummyJSON's input aliases.
    model_config = ConfigDict(json_schema_mode_override="serialization")

    id: int = Field(gt=0, strict=True)
    name: str = Field(validation_alias="title", min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    description: str
    category: str | None = None
    brand: str | None = None
    sku: str | None = None
    stock: int | None = Field(default=None, ge=0, strict=True)
    rating: float | None = Field(default=None, allow_inf_nan=False)
    discount_percentage: float | None = Field(
        default=None, validation_alias="discountPercentage", allow_inf_nan=False
    )
    availability_status: str | None = Field(default=None, validation_alias="availabilityStatus")
    minimum_order_quantity: int | None = Field(
        default=None, validation_alias="minimumOrderQuantity", ge=0, strict=True
    )
    warranty_information: str | None = Field(default=None, validation_alias="warrantyInformation")
    shipping_information: str | None = Field(default=None, validation_alias="shippingInformation")
    return_policy: str | None = Field(default=None, validation_alias="returnPolicy")
    thumbnail: str | None = None
    images: list[str] | None = None
    tags: list[str] | None = None


class ProductSearchPayload(BaseModel):
    """Required upstream fields; a missing products list is not an empty search."""

    products: list[Product]
    total: int = Field(ge=0, strict=True)
    skip: int = Field(ge=0, strict=True)
    limit: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def validate_page(self) -> "ProductSearchPayload":
        if self.skip != 0 or len(self.products) > self.limit or len(self.products) > self.total:
            raise ValueError("Inconsistent search pagination.")
        if not self.products and self.total != 0:
            raise ValueError("Missing products in a nonempty search.")
        return self


class ProductSearchResult(BaseModel):
    status: Literal["success", "no_results"]
    query: str
    source: Literal["dummyjson"] = "dummyjson"
    products: list[Product]
    total: int
    skip: int
    limit: int


class ProductDetailResult(BaseModel):
    status: Literal["success"] = "success"
    source: Literal["dummyjson"] = "dummyjson"
    product: Product
