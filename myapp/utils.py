from decimal import Decimal

from django.utils import timezone


def is_offer_valid(offer):

    if not offer:
        return False

    today = timezone.now().date()

    return (
        offer.is_active and
        offer.start_date <= today <= offer.end_date
    )


def calculate_offer_price(price, offer):

    price = Decimal(str(price))

    if not is_offer_valid(offer):
        return price

    discount = (
        price *
        Decimal(str(offer.discount_percentage))
        / Decimal("100")
    )

    discounted_price = price - discount

    return discounted_price.quantize(
        Decimal("0.01")
    )


def calculate_discount_amount(price, offer):

    price = Decimal(str(price))

    discounted_price = calculate_offer_price(
        price,
        offer
    )

    discount = price - discounted_price

    return discount.quantize(
        Decimal("0.01")
    )


def calculate_shipping(subtotal):

    subtotal = Decimal(str(subtotal))
    
    if subtotal <= Decimal("0"):
        return Decimal("0")

    shipping_rules = [
        (
            Decimal("50"),
            Decimal("5")
        ),
        (
            Decimal("100"),
            Decimal("10")
        ),
    ]

    for minimum_amount, shipping_charge in shipping_rules:

        if subtotal < minimum_amount:
            return shipping_charge

    return Decimal("15")


def calculate_order_total(subtotal):

    subtotal = Decimal(str(subtotal))

    shipping_charge = calculate_shipping(
        subtotal
    )

    total = subtotal + shipping_charge

    return {
        "subtotal": subtotal.quantize(
            Decimal("0.01")
        ),
        "shipping": shipping_charge.quantize(
            Decimal("0.01")
        ),
        "total": total.quantize(
            Decimal("0.01")
        ),
    }
    
def get_product_prices(product):

    prices = []

    for variant in product.variants.all():
        if variant.price_type == "single":
            if variant.price is not None:
                prices.append(Decimal(str(variant.price)))
        else:
            for size in variant.sizes.all():
                prices.append(Decimal(str(size.price)))

    if not prices:

        zero = Decimal("0.00")

        return {
            "starting_price": zero,
            "discounted_price": zero,
            "discount_amount": zero,
            "has_offer": False,
            "discount_percentage": 0,
        }

    starting_price = min(prices)

    discounted_price = calculate_offer_price(
        starting_price,
        product.offer
    )

    discount_amount = calculate_discount_amount(
        starting_price,
        product.offer
    )

    has_offer = is_offer_valid(
        product.offer
    )

    discount_percentage = 0

    if has_offer:

        discount_percentage = (
            product.offer.discount_percentage
        )

    return {
        "starting_price": starting_price.quantize(
            Decimal("0.01")
        ),
        "discounted_price": discounted_price.quantize(
            Decimal("0.01")
        ),
        "discount_amount": discount_amount.quantize(
            Decimal("0.01")
        ),
        "has_offer": has_offer,
        "discount_percentage": discount_percentage,
    }
from rest_framework.throttling import SimpleRateThrottle


class AuthRateThrottle(SimpleRateThrottle):
    scope = "auth"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class SearchRateThrottle(SimpleRateThrottle):
    scope = "search"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = str(user.pk) if user and user.is_authenticated else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
