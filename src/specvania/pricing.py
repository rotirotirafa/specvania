"""Optional user-maintained reference prices; no prices or currencies are shipped.

The TOML document contains one ``[[prices]]`` table per model. Every entry
requires model, input_per_mtok, output_per_mtok, currency, source_url and
checked_on (a TOML date or ISO date string). Rates are per million tokens.
"""

import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    model_validator,
)

from specvania.config import NonEmptyText


class PricingError(Exception):
    """The player can fix the pricing file; diagnostics never echo its contents."""


type Rate = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class PriceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    model: NonEmptyText
    input_per_mtok: Rate
    output_per_mtok: Rate
    currency: NonEmptyText
    source_url: HttpUrl
    checked_on: date

    @model_validator(mode="after")
    def no_source_credentials(self) -> "PriceEntry":
        if self.source_url.username is not None or self.source_url.password is not None:
            raise ValueError("Source URLs must not contain credentials")
        return self

    def coins(self, tokens_in: int, tokens_out: int) -> Decimal:
        return (
            self.input_per_mtok * tokens_in + self.output_per_mtok * tokens_out
        ) / Decimal(1_000_000)


class Pricing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    prices: tuple[PriceEntry, ...] = ()

    @model_validator(mode="after")
    def unique_models(self) -> "Pricing":
        if len({entry.model for entry in self.prices}) != len(self.prices):
            raise ValueError("Each model must have exactly one price entry")
        return self

    def for_model(self, model: str) -> PriceEntry | None:
        return next((entry for entry in self.prices if entry.model == model), None)


def load_pricing(path: Path) -> Pricing:
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except FileNotFoundError:
        return Pricing()
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise PricingError(
            "Cannot read pricing.toml; check TOML and permissions."
        ) from None
    try:
        return Pricing.model_validate(document)
    except ValidationError:
        raise PricingError(
            "Invalid pricing.toml; each [[prices]] entry needs a unique model, "
            "nonnegative finite input_per_mtok and output_per_mtok, currency, "
            "HTTP(S) source_url without credentials and checked_on date."
        ) from None
