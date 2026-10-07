from datetime import timedelta
from decimal import Decimal

from django.core.mail import send_mail
from django.conf import settings
from django.db import IntegrityError
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    IntegerField,
    Min,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import (
    urlsafe_base64_encode,
    urlsafe_base64_decode
)
from django.utils.encoding import force_bytes
from django.utils import timezone

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.http import HttpResponse, JsonResponse
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

@api_view(["GET"])
def health_check(request):
    return JsonResponse({"status": "ok", "message": "API is healthy"})

from .serializers import *

from .models import *


from rest_framework.permissions import IsAuthenticated
from .permissions import IsSuperAdmin

from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)

from google.oauth2 import id_token
from google.auth.transport import requests

import csv

import logging
from google.auth.exceptions import GoogleAuthError
from .utils import AuthRateThrottle, SearchRateThrottle

logger = logging.getLogger(__name__)


class PublicProductPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 100


def _catalog_price_expression():
    price_field = DecimalField(max_digits=10, decimal_places=2)
    return Coalesce(
        Min(
            Case(
                When(variants__price_type="single", then=F("variants__price")),
                When(
                    variants__price_type="multiple",
                    then=F("variants__sizes__price"),
                ),
                output_field=price_field,
            )
        ),
        Value(Decimal("0.00")),
        output_field=price_field,
    )


def _order_public_products(products, sort=None):
    if sort in ("price_low", "price_high"):
        products = products.annotate(_catalog_price=_catalog_price_expression())
        return products.order_by(
            "_catalog_price" if sort == "price_low" else "-_catalog_price",
            "-created_at",
        )
    if sort == "new":
        return products.order_by("-created_at")
    return products.order_by("-created_at")


def _paginate_public_products(request, products):
    paginator = PublicProductPagination()
    page = paginator.paginate_queryset(products, request)
    serializer = ProductSerializer(page, many=True, context={"request": request})
    return paginator.get_paginated_response(serializer.data)


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [AuthRateThrottle]

@api_view(["POST"])
@throttle_classes([AuthRateThrottle])
def google_login(request):

    token = request.data.get("token")
    login_type = request.data.get("login_type", "customer")

    if login_type not in ("customer", "super_admin"):
        return Response(
            {"detail": "Invalid login type."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not token:
        return Response(
            {"error": "Google token is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:

        info = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=30
        )

        email = info.get("email")
        first_name = info.get("given_name", "")

        if not email:
            return Response(
                {"error": "Google account email not found."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user, created = User.objects.get_or_create(
            email=email.lower(),
            defaults={
                "first_name": first_name,
                "is_active": True,
            }
        )

        if created:
            user.set_unusable_password()
            user.save()

        if login_type == "customer" and (user.role != "Customer" or user.is_staff or user.is_superuser):
            return Response(
                {"detail": "Admin accounts must use the Super Admin login page."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if login_type == "super_admin" and user.role != "Super Admin":
            return Response(
                {"detail": "Only Super Admin accounts can access this login."},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "is_staff": user.is_staff,
                    "role": user.role,
                    "redirect_to": (
                        "/eehook-dashboard"
                        if user.role == "Super Admin"
                        else "/"
                    ),
                }
            }
        )

    except GoogleAuthError:
        return Response(
            {
                "error": "Google authentication failed. Please try again."
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    except Exception:
        logger.exception("Google authentication provider failure")

        return Response(
            {
                "error": "Something went wrong. Please try again later."
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["POST"])
@throttle_classes([AuthRateThrottle])
def register(request):

    serializer = RegisterSerializer(data=request.data)

    serializer.is_valid(raise_exception=True)

    serializer.save()

    return Response(
        {
            "message": "Registration successful."
        },
        status=status.HTTP_201_CREATED
    )


@api_view(["POST"])
@throttle_classes([AuthRateThrottle])
def login(request):

    serializer = LoginSerializer(data=request.data)

    serializer.is_valid(raise_exception=True)

    user = serializer.validated_data["user"]
    login_type = request.data.get("login_type", "customer")

    if login_type not in ("customer", "super_admin"):
        return Response(
            {"detail": "Invalid login type."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if login_type == "customer" and (user.role != "Customer" or user.is_staff or user.is_superuser):
        return Response(
            {"detail": "Admin accounts must use the Super Admin login page."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if login_type == "super_admin" and user.role != "Super Admin":
        return Response(
            {"detail": "Only Super Admin accounts can access this login."},
            status=status.HTTP_403_FORBIDDEN,
        )

    refresh = RefreshToken.for_user(user)

    return Response(
        {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "is_staff": user.is_staff,
                "role": user.role,
                "redirect_to": (
                        "/eehook-dashboard"
                    if user.role == "Super Admin"
                    else "/"
                ),
            },
        "role": user.role,
        "redirect_to": (
            "/eehook-dashboard" if user.role == "Super Admin" else "/"
        ),
        },
        status=status.HTTP_200_OK
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request):
    """Revoke the submitted refresh token when token blacklisting is enabled."""
    refresh = request.data.get("refresh")
    if refresh:
        try:
            RefreshToken(refresh).blacklist()
        except Exception:
            return Response({"message": "Invalid refresh token."}, status=400)
    return Response(
        {"message": "Logged out successfully."},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@throttle_classes([AuthRateThrottle])
def forgot_password(request):

    serializer = ForgotPasswordSerializer(
        data=request.data
    )

    if serializer.is_valid():

        user = serializer.validated_data["user"]

        uidb64 = urlsafe_base64_encode(
            force_bytes(user.pk)
        )

        token = default_token_generator.make_token(
            user
        )

        reset_link = ( f"{settings.SITE_URL}/reset-password/{uidb64}/{token}")

        send_mail(
            subject="Reset Your Password",
            message=f"""
            Hello {user.first_name},

            Click the link below to reset your password:

            {reset_link}

            Thank You,
            Amora Team
            """,
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=[user.email],
            fail_silently=False
        )

        return Response(
            {
                "message":
                "Password reset email sent"
            }
        )

    # Do not disclose whether an account exists.
    return Response({"message": "If the account exists, a reset email will be sent."}, status=200)
@api_view(["POST"])
def reset_password( request,uidb64,token):

    serializer = ResetPasswordSerializer( data=request.data )

    if serializer.is_valid():

        try:

            uid = urlsafe_base64_decode( uidb64 ).decode()

            user = User.objects.get( pk=uid )

        except Exception:

            return Response( { "message": "Invalid Link"}, status=400 )

        if not default_token_generator.check_token( user, token ):
            
            return Response({ "message": "Invalid Token" },status=400)

        user.set_password( serializer.validated_data[ "password" ])

        user.save()

        return Response({ "message": "Password Reset Successful" } )

    return Response( serializer.errors, status=400 )
    
    
    
    
    
    
    
@api_view(["GET"])
def get_categories(request):  #NAVBAR IL ULLA CATOGARY DROPDOWN IL 

    categories = Category.objects.filter( is_active=True )

    serializer = CategorySerializer( categories, many=True )

    return Response( serializer.data )

@api_view(["GET"])    
def category_products( request, category_id ):  #CATOGRY YILE PRODUCTS PAGE IL

    products = Product.objects.filter( category_id=category_id, is_active=True )
    return _paginate_public_products(request, products.order_by("-created_at"))


@api_view(["GET"])
def get_products(request):

    products = Product.objects.filter(
        is_active=True
    )

    category = request.GET.get("category")
    subcategory = request.GET.get("subcategory")
    brand = request.GET.get("brand") or request.GET.get("brand_id")
    sort = request.GET.get("sort")

    # IDs are parsed as integers before they reach the ORM; ordering is a
    # closed allow-list and never comes from the request directly.
    try:
        category = int(category) if category else None
        subcategory = int(subcategory) if subcategory else None
        if category is not None and category < 1 or subcategory is not None and subcategory < 1:
            raise ValueError
    except (TypeError, ValueError):
        return Response({"error": "Invalid category or subcategory"}, status=400)
    if sort not in (None, "", "price_low", "price_high", "new"):
        return Response({"error": "Invalid sort value"}, status=400)

    if category:
        products = products.filter(
            category_id=category
        )

    if subcategory:
        products = products.filter(
            subcategory_id=subcategory
        )

    if brand:
        brand = str(brand).strip()
        if brand.isdigit():
            if int(brand) < 1:
                return Response({"error": "Invalid brand"}, status=400)
            products = products.filter(brand_id=int(brand), brand__is_active=True)
        else:
            products = products.filter(brand__slug=brand.lower(), brand__is_active=True)

    products = _order_public_products(products, sort).distinct()
    return _paginate_public_products(request, products)
    
    
@api_view(["GET"])
def product_details( request,pk): #PRODUCT DETAIL PAGE IL SIZE UM COLOR SELET CHEYYAM 

    try:

        product = (
            Product.objects
            .select_related("category", "subcategory", "brand", "offer")
            .prefetch_related(
                Prefetch(
                    "variants",
                    queryset=(
                        ProductVariant.objects
                        .select_related("color")
                        .prefetch_related("images", "sizes__unit__unit_type")
                    ),
                )
            )
            .get(id=pk, is_active=True)
        )
        
        # Track unique view only if not a bot
        from .models import ProductView
        from django.db.models import F
        
        user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
        bot_keywords = [
            'bot', 'crawl', 'spider', 'slurp', 'mediapartners', 'whatsapp', 
            'facebook', 'twitter', 'discord', 'telegram', 'chatgpt', 
            'openai', 'claude', 'gemini', 'anthropic'
        ]
        is_bot = any(keyword in user_agent for keyword in bot_keywords)
        
        if not is_bot:
            has_viewed = False
            
            # Check if user is authenticated
            if request.user and request.user.is_authenticated:
                # Check if this user already viewed
                has_viewed = ProductView.objects.filter(product=product, user=request.user).exists()
                if not has_viewed:
                    ProductView.objects.create(product=product, user=request.user)
            else:
                # Get IP address
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    ip = x_forwarded_for.split(',')[0]
                else:
                    ip = request.META.get('REMOTE_ADDR')
                    
                if ip:
                    has_viewed = ProductView.objects.filter(product=product, ip_address=ip).exists()
                    if not has_viewed:
                        ProductView.objects.create(product=product, ip_address=ip)
            
            if not has_viewed:
                product.current_viewers_count = F('current_viewers_count') + 1
                product.save(update_fields=['current_viewers_count'])
                product.refresh_from_db()

    except Product.DoesNotExist:

        return Response(
            {
                "message":
                "Product not found"
            },
            status=404
        )

    serializer = ProductSerializer(product, context={"request": request})

    return Response(
        serializer.data
    )
    
@api_view(["GET"])
def related_products(
    request,
    pk
):

    try:

        product = Product.objects.get(
            id=pk
        )

    except Product.DoesNotExist:

        return Response(
            {
                "message":
                "Product not found"
            },
            status=404
        )

    products = Product.objects.filter(

        category=product.category,

        is_active=True

    ).exclude(
        id=pk
    )

    return _paginate_public_products(request, products.order_by("-created_at"))
    
@api_view(["GET"])
def new_arrivals(request):

    products = Product.objects.filter(
        is_active=True
    ).order_by(
        "-created_at"
    )[:8]

    serializer = ProductSerializer(
        products,
        many=True
    )

    return Response(
        serializer.data
    )


HOMEPAGE_PRODUCT_LIMIT = 16
TRENDING_LOOKBACK_DAYS = 30


def _homepage_product_queryset(products=None):
    """Return the small, eager-loaded catalog queryset used by home cards."""
    if products is None:
        products = Product.objects.filter(is_active=True)

    variant_queryset = (
        ProductVariant.objects
        .select_related("color")
        .prefetch_related("images", "sizes__unit__unit_type")
        .order_by("id")
    )
    return (
        products
        .filter(is_active=True)
        .filter(
            Q(variants__price_type="single", variants__price__isnull=False)
            | Q(variants__price_type="multiple", variants__sizes__price__isnull=False)
        )
        .select_related("category", "subcategory", "brand", "offer")
        .prefetch_related(Prefetch("variants", queryset=variant_queryset))
        .distinct()
    )


def _successful_sales_subquery():
    """Quantity sold from paid orders, excluding cancelled orders."""
    return (
        OrderItem.objects
        .filter(
            product=OuterRef("pk"),
            order__payment_status="Paid",
            order__status__in=("Pending", "Processing", "Shipped", "Delivered"),
        )
        .values("product")
        .annotate(total=Sum("quantity"))
        .values("total")
    )


def _activity_count_subquery(model, filters, group_field):
    return (
        model.objects
        .filter(**filters)
        .values(group_field)
        .annotate(total=Count("id"))
        .values("total")
    )


def _trending_products():
    since = timezone.now() - timedelta(days=TRENDING_LOOKBACK_DAYS)
    recent_views = _activity_count_subquery(
        ProductView,
        {"product": OuterRef("pk"), "created_at__gte": since},
        "product",
    )
    wishlist_activity = _activity_count_subquery(
        Wishlist,
        {"variant__product": OuterRef("pk")},
        "variant__product",
    )
    cart_activity = _activity_count_subquery(
        Cart,
        {"variant__product": OuterRef("pk")},
        "variant__product",
    )
    sales = _successful_sales_subquery()

    return (
        _homepage_product_queryset()
        .annotate(
            _recent_views=Coalesce(
                Subquery(recent_views, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
            _wishlist_activity=Coalesce(
                Subquery(wishlist_activity, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
            _cart_activity=Coalesce(
                Subquery(cart_activity, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
            _sales_quantity=Coalesce(
                Subquery(sales, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
        )
        .annotate(
            _trending_score=(
                F("_recent_views")
                + F("_wishlist_activity") * Value(3)
                + F("_cart_activity") * Value(4)
                + F("_sales_quantity") * Value(6)
            )
        )
        .order_by("-_trending_score", "-updated_at", "-id")[:HOMEPAGE_PRODUCT_LIMIT]
    )


def _top_deal_products():
    today = timezone.localdate()
    return (
        _homepage_product_queryset()
        .filter(
            offer__is_active=True,
            offer__start_date__lte=today,
            offer__end_date__gte=today,
        )
        .annotate(_catalog_price=_catalog_price_expression())
        .order_by("-offer__discount_percentage", "_catalog_price", "-updated_at", "-id")[
            :HOMEPAGE_PRODUCT_LIMIT
        ]
    )


def _best_seller_products():
    return (
        _homepage_product_queryset()
        .annotate(
            _sales_quantity=Coalesce(
                Subquery(_successful_sales_subquery(), output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            )
        )
        .filter(_sales_quantity__gt=0)
        .order_by("-_sales_quantity", "-updated_at", "-id")[:HOMEPAGE_PRODUCT_LIMIT]
    )


def _new_homepage_products():
    return _homepage_product_queryset().order_by("-created_at", "-id")[:HOMEPAGE_PRODUCT_LIMIT]


def _request_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _recently_viewed_products(request):
    if request.user and request.user.is_authenticated:
        views = ProductView.objects.filter(user=request.user)
    else:
        ip_address = _request_ip(request)
        if not ip_address:
            return Product.objects.none()
        views = ProductView.objects.filter(user__isnull=True, ip_address=ip_address)

    # ProductView is intentionally not unique in the database because the same
    # product can be viewed by an authenticated user and an IP. Deduplicate in
    # the small recent-ID list before querying the product catalog.
    product_ids = []
    for product_id in views.order_by("-created_at", "-id").values_list("product_id", flat=True)[:64]:
        if product_id not in product_ids:
            product_ids.append(product_id)
        if len(product_ids) == HOMEPAGE_PRODUCT_LIMIT:
            break

    if not product_ids:
        return Product.objects.none()

    preserved_order = Case(
        *[When(pk=product_id, then=position) for position, product_id in enumerate(product_ids)],
        output_field=IntegerField(),
    )
    return _homepage_product_queryset(
        Product.objects.filter(pk__in=product_ids)
    ).annotate(_recent_order=preserved_order).order_by("_recent_order")


def _user_signal_product_ids(user):
    ids = []
    sources = (
        ProductView.objects.filter(user=user).order_by("-created_at").values_list("product_id", flat=True)[:32],
        Wishlist.objects.filter(user=user).values_list("variant__product_id", flat=True)[:32],
        Cart.objects.filter(user=user).values_list("variant__product_id", flat=True)[:32],
        OrderItem.objects.filter(
            order__user=user,
            order__payment_status="Paid",
            order__status__in=("Pending", "Processing", "Shipped", "Delivered"),
        ).values_list("product_id", flat=True)[:32],
    )
    for source in sources:
        for product_id in source:
            if product_id and product_id not in ids:
                ids.append(product_id)
            if len(ids) >= 64:
                return ids
    return ids


def _just_for_you_products(request):
    selected = []
    selected_ids = set()
    signal_ids = []
    if request.user and request.user.is_authenticated:
        signal_ids = _user_signal_product_ids(request.user)

    if signal_ids:
        signal_products = Product.objects.filter(pk__in=signal_ids).values(
            "category_id", "subcategory_id", "brand_id"
        )
        category_ids = set()
        subcategory_ids = set()
        brand_ids = set()
        for signal in signal_products:
            if signal["category_id"]:
                category_ids.add(signal["category_id"])
            if signal["subcategory_id"]:
                subcategory_ids.add(signal["subcategory_id"])
            if signal["brand_id"]:
                brand_ids.add(signal["brand_id"])

        related_filter = Q(pk__in=[])
        score_parts = []
        if category_ids:
            related_filter |= Q(category_id__in=category_ids)
            score_parts.append(("_category_match", category_ids, 2))
        if subcategory_ids:
            related_filter |= Q(subcategory_id__in=subcategory_ids)
            score_parts.append(("_subcategory_match", subcategory_ids, 3))
        if brand_ids:
            related_filter |= Q(brand_id__in=brand_ids)
            score_parts.append(("_brand_match", brand_ids, 3))

        if score_parts:
            related = _homepage_product_queryset().filter(related_filter).exclude(pk__in=signal_ids)
            score_annotations = {}
            score_expression = Value(0)
            for name, ids, weight in score_parts:
                score_annotations[name] = Case(
                    When(**{name.removeprefix("_").replace("_match", "_id") + "__in": ids}, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
                score_expression += F(name) * Value(weight)
            related = related.annotate(**score_annotations).annotate(
                _recommendation_score=score_expression
            ).order_by("-_recommendation_score", "-updated_at", "-id")[:HOMEPAGE_PRODUCT_LIMIT]
            for product in related:
                selected.append(product)
                selected_ids.add(product.id)

    # Guests and users without enough useful signals get real catalog
    # fallbacks. The same product is never repeated inside this section.
    for queryset in (_trending_products(), _best_seller_products(), _new_homepage_products()):
        if len(selected) >= HOMEPAGE_PRODUCT_LIMIT:
            break
        for product in queryset:
            if product.id not in selected_ids:
                selected.append(product)
                selected_ids.add(product.id)
            if len(selected) >= HOMEPAGE_PRODUCT_LIMIT:
                break
    return selected[:HOMEPAGE_PRODUCT_LIMIT]


def _homepage_brands():
    product_filter = Q(products__is_active=True)
    return (
        Brand.objects
        .filter(is_active=True)
        .annotate(product_count=Count("products", filter=product_filter, distinct=True))
        .filter(product_count__gt=0)
        .order_by("-product_count", "name")[:HOMEPAGE_PRODUCT_LIMIT]
    )


def _homepage_trust_benefits():
    return HomepageTrustBenefitSerializer(
        TrustBenefit.objects.filter(is_active=True).order_by("display_order", "id"),
        many=True,
    ).data


def _homepage_product_data(products, request):
    return HomepageProductSerializer(
        products,
        many=True,
        context={"request": request},
    ).data


@api_view(["GET"])
def homepage(request):
    categories = (
        Category.objects
        .filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "subcategories",
                queryset=SubCategory.objects.filter(is_active=True).order_by("name"),
            )
        )
    )
    recently_viewed = _recently_viewed_products(request)

    return Response(
        {
            "limits": {"products_per_section": HOMEPAGE_PRODUCT_LIMIT},
            "hero_banners": HeroBannerSerializer(
                HeroBanner.objects.filter(is_active=True).order_by("display_order"),
                many=True,
                context={"request": request},
            ).data,
            "categories": HomeCategorySerializer(
                categories,
                many=True,
                context={"request": request},
            ).data,
            "new_arrivals": _homepage_product_data(_new_homepage_products(), request),
            "trending_now": _homepage_product_data(_trending_products(), request),
            "top_deals": _homepage_product_data(_top_deal_products(), request),
            "best_sellers": _homepage_product_data(_best_seller_products(), request),
            "just_for_you": _homepage_product_data(_just_for_you_products(request), request),
            "shop_by_brand": HomepageBrandSerializer(
                _homepage_brands(),
                many=True,
                context={"request": request},
            ).data,
            "recently_viewed": _homepage_product_data(recently_viewed, request),
            "trust_benefits": _homepage_trust_benefits(),
        }
    )


@api_view(["GET"])
def homepage_brands(request):
    return Response(
        HomepageBrandSerializer(
            _homepage_brands(),
            many=True,
            context={"request": request},
        ).data
    )


@api_view(["GET"])
def recently_viewed(request):
    return Response(
        _homepage_product_data(_recently_viewed_products(request), request)
    )
    
@api_view(["GET"])
def get_subcategories(request):

    subcategories = SubCategory.objects.filter( is_active=True )

    serializer = SubCategorySerializer( subcategories, many=True )

    return Response( serializer.data )

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_to_wishlist(request):

    user = request.user

    variant_id = request.data.get("variant")
    variant_unit_id = request.data.get("variant_size")

    if not variant_id:

        return Response(
            {
                "error": "Variant is required"
            },
            status=400
        )

    try:

        variant = ProductVariant.objects.get(
            id=variant_id
        )

    except ProductVariant.DoesNotExist:

        return Response(
            {
                "error": "Variant not found"
            },
            status=404
        )

    variant_unit = None
    try:
        if variant_unit_id:
            variant_unit = ProductVariantUnit.objects.get(id=variant_unit_id, variant=variant)
        else:
            variant_unit = ProductVariantUnit.objects.filter(variant=variant).first()
    except ProductVariantUnit.DoesNotExist:
        return Response({"error": "Variant unit not found"}, status=404)

    wishlist, created = Wishlist.objects.get_or_create(

        user=user,
        variant=variant,
        variant_unit=variant_unit

    )

    if created:

        return Response(
            {
                "message": "Added to wishlist"
            },
            status=201
        )

    return Response(
        {
            "message": "Already in wishlist"
        },
        status=200
    )
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_wishlist(request):

    wishlist = Wishlist.objects.filter(
        user=request.user
    ).select_related(
        "variant",
        "variant__product",
        "variant__color",
        "variant_unit",
        "variant_unit__unit",
        "variant__product__category",
        "variant__product__offer"
    )

    serializer = WishlistSerializer(
        wishlist,
        many=True
    )

    return Response(
        serializer.data
    )
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def remove_wishlist(request, id):

    wishlist = Wishlist.objects.get(
        id=id,
        user=request.user
    )

    wishlist.delete()

    return Response(
        {
            "message": "Removed from wishlist"
        }
    )
def _refresh_cart_coupon(cart_item):
    """Reload and validate a cart coupon, detaching stale coupons."""
    if not cart_item.coupon_id:
        return None

    coupon_id = cart_item.coupon_id
    coupon = get_eligible_coupon(coupon_id, cart_item.variant.product)
    if coupon:
        cart_item.coupon = coupon
        return coupon

    Cart.objects.filter(pk=cart_item.pk, coupon_id=coupon_id).update(coupon=None)
    SavedCoupon.objects.filter(
        user=cart_item.user,
        product=cart_item.variant.product,
        coupon_id=coupon_id,
    ).delete()
    cart_item.coupon = None
    cart_item.coupon_status = "COUPON_INACTIVE"
    return None


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_to_cart(request):

    print("USER =", request.user)
    print("AUTH =", request.auth)

    variant_id = request.data.get(
        "variant"
    )

    variant_unit_id = request.data.get(
        "variant_size"
    )

    try:

        quantity = int(
            request.data.get(
                "quantity",
                1
            )
        )

    except (TypeError, ValueError):

        return Response(
            {
                "message":
                "Invalid quantity"
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    if quantity < 1:

        return Response(
            {
                "message":
                "Quantity must be greater than 0"
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    try:

        variant = ProductVariant.objects.select_related(
            "product"
        ).get(
            id=variant_id
        )

        variant_unit = None
        if variant_unit_id:
            variant_unit = ProductVariantUnit.objects.select_related("variant").get(id=variant_unit_id)
            if variant.price_type != "multiple":
                return Response(
                    {"message": "A unit can only be selected for a multiple-price product"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if variant_unit.variant_id != variant.id:
                return Response(
                    {"message": "Invalid unit selected"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif variant.price_type == "multiple":
            variant_unit = ProductVariantUnit.objects.select_related("variant").filter(variant=variant).first()

    except ProductVariant.DoesNotExist:
        return Response({"message": "Variant not found"}, status=status.HTTP_404_NOT_FOUND)
    except ProductVariantUnit.DoesNotExist:
        return Response({"message": "Variant unit not found"}, status=status.HTTP_404_NOT_FOUND)

    if variant.price_type == "multiple":
        if not variant_unit:
            return Response({"message": "Out of stock"}, status=status.HTTP_400_BAD_REQUEST)
        if variant_unit.variant != variant:
            return Response({"message": "Invalid unit selected"}, status=status.HTTP_400_BAD_REQUEST)
        available_stock = variant_unit.stock
    else:
        available_stock = variant.stock

    if quantity > available_stock:
        return Response({"message": f"Only {available_stock} items available in stock"}, status=status.HTTP_400_BAD_REQUEST)

    saved_coupons = SavedCoupon.objects.filter(
        user=request.user,
        product=variant.product,
    ).select_related("coupon", "coupon__category")
    coupon = None
    for saved_coupon in saved_coupons:
        current_coupon = get_eligible_coupon(saved_coupon.coupon_id, variant.product)
        if current_coupon:
            coupon = current_coupon
            break
        SavedCoupon.objects.filter(pk=saved_coupon.pk).delete()

    # A category coupon is dynamic: once the customer applies it to one
    # product in a category, it can be carried to other eligible products
    # added to the cart without creating product targets in the coupon.
    if coupon is None:
        category_coupons = SavedCoupon.objects.filter(
            user=request.user,
            coupon__applicability_type=Coupon.APPLICABILITY_CATEGORY,
            coupon__category_id=variant.product.category_id,
        ).select_related("coupon", "coupon__category")
        for saved_coupon in category_coupons:
            current_coupon = get_eligible_coupon(saved_coupon.coupon_id, variant.product)
            if current_coupon:
                coupon = current_coupon
                break
            SavedCoupon.objects.filter(pk=saved_coupon.pk).delete()

    # Usage is scoped to the product. Using the same coupon on another
    # product must remain possible for the same customer.
    if coupon and CouponUsage.objects.filter(
        coupon=coupon,
        user=request.user,
        product=variant.product,
    ).exists():
        coupon = None

    cart_item, created = Cart.objects.get_or_create(

        user=request.user,

        variant=variant,

        variant_unit=variant_unit,

        defaults={
            "quantity": quantity,
            "coupon": coupon
        }

    )

    if not created:

        new_quantity = cart_item.quantity + quantity

        if new_quantity > available_stock:

            return Response(
                {
                    "message":
                    f"Only {available_stock} items available in stock"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        existing_coupon = _refresh_cart_coupon(cart_item)
        if coupon is None and existing_coupon:
            coupon = existing_coupon
        cart_item.quantity = new_quantity
        if coupon:
            cart_item.coupon = coupon
        elif cart_item.coupon_id:
            cart_item.coupon = None
        cart_item.save()

    return Response(
        {
            "message":
            "Product added to cart successfully"
        },
        status=status.HTTP_200_OK
    )
    
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_cart(request):

    print("USER =", request.user)
    print("AUTH =", request.auth)

    cart = (
        Cart.objects
        .filter(user=request.user)
        .select_related(
            "variant",
            "variant__product",
            "variant__product__offer",
            "variant__color",
            "variant_unit",
            "variant_unit__unit",
        )
        .prefetch_related(
            "variant__images"
        )
    )

    cart_items = list(cart)
    current_coupons = {
        item.id: _refresh_cart_coupon(item)
        for item in cart_items
        if item.coupon_id
    }

    serializer = CartSerializer(
        cart_items,
        many=True
    )

    subtotal = Decimal("0.00")

    total_items = 0

    for item in cart_items:

        price = (item.variant.price or 0) if item.variant.price_type == "single" else ((item.variant_unit.price or 0) if item.variant_unit else 0)
        discounted_price = calculate_offer_price(
            price,
            item.variant.product.offer
        )
        coupon = current_coupons.get(item.id)
        if coupon:
            discounted_price = calculate_coupon_price(discounted_price, coupon)

        subtotal += (
            discounted_price *
            item.quantity
        )

        total_items += item.quantity

    totals = calculate_order_total(
        subtotal
    )

    return Response(
        {
            "items": serializer.data,
            "subtotal": totals["subtotal"],
            "shipping": totals["shipping"],
            "total": totals["total"],
            "total_items": total_items,
        },
        status=status.HTTP_200_OK
    )
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_cart_quantity(request, id):

    try:

        cart = Cart.objects.select_related(
            "variant_unit"
        ).get(
            id=id,
            user=request.user
        )

    except Cart.DoesNotExist:

        return Response(
            {
                "message":
                "Cart item not found"
            },
            status=status.HTTP_404_NOT_FOUND
        )

    current_coupon = _refresh_cart_coupon(cart)

    try:

        quantity = int(
            request.data.get(
                "quantity"
            )
        )

    except (TypeError, ValueError):

        return Response(
            {
                "message":
                "Invalid quantity"
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    if quantity < 1:

        return Response(
            {
                "message":
                "Quantity must be greater than 0"
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    available_stock = cart.variant_unit.stock if cart.variant.price_type == "multiple" and cart.variant_unit else cart.variant.stock

    if quantity > available_stock:

        return Response(
            {
                "message":
                f"Only {available_stock} items available in stock"
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    cart.quantity = quantity

    cart.save()

    original_price = (
        cart.variant.price
        if cart.variant.price_type == "single"
        else (cart.variant_unit.price if cart.variant_unit else 0)
    )
    discounted_price = calculate_offer_price(
        original_price or 0,
        cart.variant.product.offer
    )
    if current_coupon:
        discounted_price = calculate_coupon_price(discounted_price, current_coupon)

    line_total = (
        discounted_price *
        cart.quantity
    )

    cart_items = (
        Cart.objects
        .filter(user=request.user)
        .select_related(
            "variant__product__offer",
            "variant_unit"
        )
    )

    subtotal = Decimal("0.00")

    total_items = 0

    for item in cart_items:

        item_coupon = _refresh_cart_coupon(item)

        item_price = (
            item.variant.price
            if item.variant.price_type == "single"
            else (item.variant_unit.price if item.variant_unit else 0)
        )
        item_discounted_price = calculate_offer_price(
            item_price or 0,
            item.variant.product.offer,
        )
        if item_coupon:
            item_discounted_price = calculate_coupon_price(item_discounted_price, item_coupon)
        subtotal += item_discounted_price * item.quantity

        total_items += item.quantity

    totals = calculate_order_total(
        subtotal
    )

    return Response(
        {
            "message":
            "Quantity updated successfully",

            "quantity":
            cart.quantity,

            "item_total":
            line_total,

            "subtotal":
            totals["subtotal"],

            "shipping":
            totals["shipping"],

            "coupon_status": getattr(cart, "coupon_status", None),

            "total":
            totals["total"],

            "total_items":
            total_items
        },
        status=status.HTTP_200_OK
    )

from django.db import transaction
from .whatsapp import send_owner_order_notification

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def place_order(request):

    address_id = request.data.get(
        "address"
    )

    try:

        address = Address.objects.get(
            id=address_id,
            user=request.user
        )

    except Address.DoesNotExist:

        return Response(
            {
                "message":
                "Address not found"
            },
            status=status.HTTP_404_NOT_FOUND
        )

    cart_items = (
        Cart.objects
        .select_for_update()
        .select_related(
            "variant__product__offer",
            "variant__color",
            "variant_unit__unit"
        )
        .filter(
            user=request.user
        )
        .order_by("id")
    )

    if not cart_items.exists():

        return Response(
            {
                "message":
                "Cart is empty"
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    original_subtotal = Decimal("0.00")

    discount_total = Decimal("0.00")

    discounted_subtotal = Decimal("0.00")

    for item in cart_items:

        # Lock the exact inventory row before checking and reserving stock.
        # This prevents concurrent orders from overselling the same SKU.
        if item.variant.price_type == "multiple" and item.variant_unit_id:
            item.variant_unit = ProductVariantUnit.objects.select_for_update().get(
                pk=item.variant_unit_id
            )
        else:
            item.variant = ProductVariant.objects.select_for_update().get(
                pk=item.variant_id
            )

        available_stock = item.variant_unit.stock if item.variant.price_type == "multiple" and item.variant_unit else item.variant.stock

        if item.quantity > available_stock:

            return Response(
                {
                    "message":
                    f"Only {available_stock} items available for {item.variant.product.name}"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        original_price = (item.variant.price or 0) if item.variant.price_type == "single" else ((item.variant_unit.price or 0) if item.variant_unit else 0)

        discounted_price = calculate_offer_price(
            original_price,
            item.variant.product.offer
        )
        current_coupon = _refresh_cart_coupon(item)
        if current_coupon:
            discounted_price = calculate_coupon_price(discounted_price, current_coupon)

        discount_amount = original_price - discounted_price

        original_subtotal += (
            original_price *
            item.quantity
        )

        discount_total += (
            discount_amount *
            item.quantity
        )

        discounted_subtotal += (
            discounted_price *
            item.quantity
        )

    totals = calculate_order_total(
        discounted_subtotal
    )

    order = Order.objects.create(

        user=request.user,

        address=address,

        subtotal=original_subtotal,

        discount_amount=discount_total,

        shipping_charge=totals["shipping"],

        total_amount=totals["total"],

        payment_status="Pending",

        status="Pending"
    )
    
    for item in cart_items:

        original_price = (item.variant.price or 0) if item.variant.price_type == "single" else ((item.variant_unit.price or 0) if item.variant_unit else 0)

        discounted_price = calculate_offer_price(
            original_price,
            item.variant.product.offer
        )
        current_coupon = _refresh_cart_coupon(item)
        if current_coupon:
            discounted_price = calculate_coupon_price(discounted_price, current_coupon)
            
            CouponUsage.objects.get_or_create(
                coupon=current_coupon,
                user=request.user,
                product=item.variant.product
            )
            SavedCoupon.objects.filter(user=request.user, coupon=current_coupon, product=item.variant.product).delete()

        discount_amount = original_price - discounted_price

        OrderItem.objects.create(

            order=order,

            product=item.variant.product,

            color=item.variant.color,

            unit=item.variant_unit.unit if item.variant_unit else None,

            quantity=item.quantity,

            original_price=original_price,

            discount_amount=discount_amount,

            price=discounted_price,

            total_price=(
                discounted_price *
                item.quantity
            ),
            variant_unit=item.variant_unit

        )

        if item.variant.price_type == "multiple" and item.variant_unit:
            item.variant_unit.stock -= item.quantity
            item.variant_unit.save()
        else:
            item.variant.stock -= item.quantity
            item.variant.save()

    cart_items.delete()

    transaction.on_commit(
        lambda order_id=order.id: send_owner_order_notification(order_id)
    )

    return Response(

        {

            "message":
            "Order placed successfully",

            "order_id":
            order.id,

            "subtotal":
            order.subtotal,

            "discount":
            order.discount_amount,

            "shipping":
            order.shipping_charge,

            "grand_total":
            order.total_amount,

            "payment_status":
            order.payment_status,

            "status":
            order.status

        },

        status=status.HTTP_201_CREATED

    )
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def remove_cart_item(
    request,
    id
):

    try:

        cart = Cart.objects.get(

            id=id,

            user=request.user

        )

    except Cart.DoesNotExist:

        return Response(

            {
                "message":
                "Cart item not found"
            },

            status=404

        )

    cart.delete()

    return Response(

        {
            "message":
            "Item removed"
        }

    )
    
 


    
from django.utils import timezone

@api_view(["GET"])
def get_offers(request):

    today = timezone.now().date()

    offers = Offer.objects.filter(

        is_active=True,

        start_date__lte=today,

        end_date__gte=today

    )

    serializer = OfferSerializer(
        offers,
        many=True
    )

    return Response(
        serializer.data
    )
    
@api_view(["GET"])
def offer_products(request):

    today = timezone.now().date()

    offer = Offer.objects.filter(

        is_active=True,

        start_date__lte=today,

        end_date__gte=today

    ).first()

    if not offer:

        return Response([])

    products = Product.objects.filter(
        offer=offer
    )

    serializer = ProductSerializer(
        products,
        many=True
    )

    return Response({

        "offer": {

            "title":
            offer.title,

            "description":
            offer.description,

            "image":
            offer.image.url
            if offer.image
            else None,

            "discount":
            offer.discount_percentage

        },

        "products":
        serializer.data

    })

@api_view(["GET"])
def offer_status(request):

    today = timezone.now().date()

    has_offer = Offer.objects.filter(

        is_active=True,

        start_date__lte=today,

        end_date__gte=today

    ).exists()

    return Response({

        "has_offer": has_offer

    })

@api_view(["GET"])
@throttle_classes([SearchRateThrottle])
def search_products(request):

        search = request.GET.get(
            "search",
            ""
        ).strip()

        category = request.GET.get(
            "category"
        )

        subcategory = request.GET.get(
            "subcategory"
        )

        offer = request.GET.get(
            "offer"
        )

        new_arrival = request.GET.get(
            "new_arrival"
        )

        sort = request.GET.get(
            "sort"
        )

        if len(search) > 100:
            return Response({"error": "Search is too long"}, status=400)
        if sort not in (None, "", "price_low", "price_high", "new"):
            return Response({"error": "Invalid sort value"}, status=400)
        try:
            category = int(category) if category else None
            subcategory = int(subcategory) if subcategory else None
            if category is not None and category < 1 or subcategory is not None and subcategory < 1:
                raise ValueError
        except (TypeError, ValueError):
            return Response({"error": "Invalid category or subcategory"}, status=400)

        products = Product.objects.filter(
            is_active=True
        )

        if search:

            products = products.filter(

                Q(
                    name__icontains=search
                )

                |

                Q(
                    description__icontains=search
                )

                |

                Q(
                    category__name__icontains=search
                )

                |

                Q(
                    subcategory__name__icontains=search
                )

            )

        if category:

            products = products.filter(
                category_id=category
            )

        if subcategory:

            products = products.filter(
                subcategory_id=subcategory
            )

        if offer == "true":

            products = products.filter(
                offer__isnull=False
            )

        if new_arrival == "true":

            latest_ids = Product.objects.filter(
                is_active=True
            ).order_by(
                "-created_at"
            ).values_list(
                "id",
                flat=True
            )[:8]

            products = products.filter(
                id__in=latest_ids
            )

        products = _order_public_products(products, sort).distinct()
        return _paginate_public_products(request, products)
    
from decimal import Decimal

from django.db import transaction

# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# @transaction.atomic
# def place_order(request):

#     address_id = request.data.get(
#         "address"
#     )

#     try:

#         address = Address.objects.get(
#             id=address_id,
#             user=request.user
#         )

#     except Address.DoesNotExist:

#         return Response(
#             {
#                 "message": "Address not found"
#             },
#             status=status.HTTP_404_NOT_FOUND
#         )

#     cart_items = Cart.objects.select_related(
#         "variant__product__offer",
#         "variant__color",
#         "variant_unit__unit",
#     ).filter(
#         user=request.user
#     )

#     if not cart_items.exists():

#         return Response(
#             {
#                 "message": "Cart is empty"
#             },
#             status=status.HTTP_400_BAD_REQUEST
#         )

#     subtotal = Decimal("0.00")

#     for item in cart_items:

#         if item.quantity > item.variant_unit.stock:

#             return Response(
#                 {
#                     "message": (
#                         f"Only "
#                         f"{item.variant_unit.stock} "
#                         f"items available for "
#                         f"{item.variant.product.name}"
#                     )
#                 },
#                 status=status.HTTP_400_BAD_REQUEST
#             )

#         discounted_price = calculate_offer_price(
#             item.variant_unit.price,
#             item.variant.product.offer
#         )

#         subtotal += (
#             discounted_price *
#             item.quantity
#         )

#     totals = calculate_order_total(
#         subtotal
#     )

#     order = Order.objects.create(

#         user=request.user,

#         address=address,

#         subtotal=totals["subtotal"],

#         discount_amount=totals["discount"],

#         shipping_charge=totals["shipping"],

#         total_amount=totals["total"],

#         payment_status="Pending",

#         status="Pending"

#     )

#     for item in cart_items:

#         prices = get_product_prices(

#             item.variant_unit.price,

#             item.variant.product.offer,

#             item.quantity

#         )

#         OrderItem.objects.create(

#             order=order,

#             product=item.variant.product,

#             color=item.variant.color,

#             unit=item.variant_unit.unit,

#             variant_unit=item.variant_unit,

#             quantity=item.quantity,

#             original_price=prices["original_price"],

#             discount_amount=prices["discount_amount"],

#             price=prices["discounted_price"],

#             total_price=prices["item_total"]

#         )

#         item.variant_unit.stock -= item.quantity

#         item.variant_unit.save()

#     cart_items.delete()

#     return Response(

#         {

#             "message": "Order placed successfully",

#             "order_id": order.id,

#             "subtotal": order.subtotal,

#             "discount": order.discount_amount,

#             "shipping": order.shipping_charge,

#             "total_amount": order.total_amount

#         },

#         status=status.HTTP_201_CREATED

#     )
    
    
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_orders(request):

    orders = Order.objects.filter(
        user=request.user
    ).order_by(
        "-created_at"
    )

    serializer = MyOrderSerializer(
        orders,
        many=True
    )

    return Response(
        serializer.data
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def cancel_order(request, id):

    try:

        order = Order.objects.get(
            id=id,
            user=request.user
        )

    except Order.DoesNotExist:

        return Response(
            {
                "message": "Order not found"
            },
            status=404
        )

    if order.status not in [
        "Pending",
        "Processing"
    ]:

        return Response(
            {
                "message": "This order cannot be cancelled."
            },
            status=400
        )

    if order.payment_status == "Paid":

        return Response(
            {
                "message": "Paid orders cannot be cancelled."
            },
            status=400
        )

    for item in order.items.all():
        if item.variant_unit:
            item.variant_unit.stock += item.quantity
            item.variant_unit.save()
        else:
            variant = ProductVariant.objects.filter(product=item.product, color=item.color).first()
            if variant:
                variant.stock += item.quantity
                variant.save()

    order.status = "Cancelled"

    order.save()

    return Response(
        {
            "message": "Order cancelled successfully."
        }
    )
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_details(request, id):

    try:

        order = Order.objects.get(
            id=id,
            user=request.user
        )

    except Order.DoesNotExist:

        return Response(
            {
                "message":
                "Order not found"
            },
            status=404
        )

    serializer = OrderSerializer(
        order
    )

    return Response(
        serializer.data
    )
    
from django.db.models import Q, Sum

@api_view(["GET"])
@permission_classes([IsSuperAdmin])
def admin_dashboard_cards(request):

    total_revenue = (
        Order.objects.filter(
            payment_status="Paid"
        ).exclude(
            status="Cancelled"
        ).aggregate(
            total=Sum("total_amount")
        )["total"] or 0
    )

    data = {

        "total_orders": Order.objects.count(),

        "revenue": total_revenue,

        "pending_orders": Order.objects.filter(
            status="Pending"
        ).count(),

        "processing_orders": Order.objects.filter(
            status="Processing"
        ).count(),

        "shipped_orders": Order.objects.filter(
            status="Shipped"
        ).count(),

        "delivered_orders": Order.objects.filter(
            status="Delivered"
        ).count(),

        "cancelled_orders": Order.objects.filter(
            status="Cancelled"
        ).count(),
        "total_revenue": total_revenue,
        "total_customers": User.objects.filter(is_active=True).count(),
        "total_products": Product.objects.count(),
        "active_coupons": Coupon.objects.filter(is_active=True).count(),

    }

    return Response(data)



@api_view(["GET"])
@permission_classes([IsSuperAdmin])
def admin_orders(request):

    if not request.user.is_staff:

        return Response(
            {
                "error": "Unauthorized"
            },
            status=403
        )

    orders = Order.objects.select_related(
        "user"
    )

    search = request.GET.get(
        "search",
        ""
    ).strip()

    status_filter = request.GET.get(
        "status"
    )

    sort = request.GET.get(
        "sort"
    )

    if search:

        if search.upper().startswith("ORD-"):

            order_id = search[4:].lstrip("0")

            if order_id == "":

                order_id = "0"

            if order_id.isdigit():

                orders = orders.filter(
                    id=int(order_id)
                )

            else:

                orders = orders.none()

        elif search.isdigit():

            orders = orders.filter(
                id=int(search)
            )

        else:

            orders = orders.filter(

                Q(
                    user__first_name__icontains=search
                )

                |

                Q(
                    user__email__icontains=search
                )

            )

    if status_filter:

        orders = orders.filter(
            status=status_filter
        )

    if sort == "newest":

        orders = orders.order_by(
            "-created_at"
        )

    elif sort == "oldest":

        orders = orders.order_by(
            "created_at"
        )

    elif sort == "high_amount":

        orders = orders.order_by(
            "-total_amount"
        )

    elif sort == "low_amount":

        orders = orders.order_by(
            "total_amount"
        )

    else:

        orders = orders.order_by(
            "-created_at"
        )

    serializer = AdminOrderSerializer(
        orders,
        many=True
    )

    return Response(
        serializer.data
    )
    
@api_view(["GET"])
@permission_classes([IsSuperAdmin])
def admin_order_details(request, id):

    if not request.user.is_staff:

        return Response(
            {
                "error": "Unauthorized"
            },
            status=403
        )

    try:

        order = Order.objects.select_related(
            "user",
            "address"
        ).prefetch_related(
            "items"
        ).get(
            id=id
        )

    except Order.DoesNotExist:

        return Response(
            {
                "error": "Order Not Found"
            },
            status=404
        )

    serializer = OrderSerializer(
        order
    )

    return Response(
        serializer.data
    )


@api_view(["PUT"])
@permission_classes([IsSuperAdmin])
def update_order_status(request, id):

    if not request.user.is_staff:

        return Response(
            {
                "error": "Unauthorized"
            },
            status=403
        )

    try:

        order = Order.objects.get(
            id=id
        )

    except Order.DoesNotExist:

        return Response(
            {
                "error": "Order Not Found"
            },
            status=404
        )

    status_value = request.data.get(
        "status"
    )

    allowed_status = [

        choice[0]

        for choice in Order.STATUS_CHOICES

    ]

    if status_value not in allowed_status:

        return Response(
            {
                "error": "Invalid Status"
            },
            status=400
        )

    order.status = status_value

    order.save()

    return Response(
        {
            "message": "Order Status Updated Successfully",
            "status": order.status
        }
    )

@api_view(["GET"])
@permission_classes([IsSuperAdmin])
def low_stock_products(request):

    if not request.user.is_staff:

        return Response(
            {
                "error": "Unauthorized"
            },
            status=403
        )

    products = ProductVariantUnit.objects.select_related(
        "variant__product",
        "variant__color",
        "variant__product__category",
        "unit"
    ).filter(
        stock__lte=5
    ).order_by(
        "stock"
    )

    data = []

    for item in products:

        data.append({

            "id": item.id,

            "product_name": item.variant.product.name,

            "category": (
                item.variant.product.category.name
                if item.variant.product.category
                else None
            ),

            "color": (
                item.variant.color.name
                if item.variant.color
                else None
            ),

            "unit": (
                item.unit.name
                if item.unit
                else None
            ),

            "stock": item.stock

        })

    return Response(data)

@api_view(["GET"])
@permission_classes([IsSuperAdmin])
def export_orders_csv(request):

    if not request.user.is_staff:

        return Response(
            {
                "error": "Unauthorized"
            },
            status=403
        )

    response = HttpResponse(
        content_type="text/csv"
    )

    response[
        "Content-Disposition"
    ] = 'attachment; filename="orders.csv"'

    writer = csv.writer(
        response
    )

    writer.writerow([

        "Order ID",

        "Customer",

        "Email",

        "Amount",

        "Payment Method",

        "Payment Status",

        "Order Status",

        "Date"

    ])

    orders = Order.objects.select_related(
        "user"
    ).order_by(
        "-created_at"
    )

    for order in orders:

        writer.writerow([

            order.id,

            order.user.first_name,

            order.user.email,

            order.total_amount,


            order.payment_status,

            order.status,

            order.created_at.strftime(
                "%d-%m-%Y %H:%M"
            )

        ])

    return response

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import UserProfile
from .serializers import ProfileSerializer


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def profile(request):

    user = request.user

    profile, created = UserProfile.objects.get_or_create(user=user)

    if request.method == "GET":

        serializer = ProfileSerializer(user)
        print(serializer.data)

        return Response(serializer.data)

    serializer = ProfileSerializer(
        user,
        data=request.data,
        partial=True
    )

    if serializer.is_valid():

        serializer.save()

        return Response(
            {
                "message": "Profile updated successfully",
                "data": serializer.data
            }
        )

    return Response(
        serializer.errors,
        status=status.HTTP_400_BAD_REQUEST
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def address_list(request):

    if request.method == "GET":

        addresses = Address.objects.filter(user=request.user).order_by("-is_default", "-id")
        serializer = AddressSerializer(addresses, many=True)
        return Response(serializer.data)

    serializer = AddressSerializer(
        data=request.data,
        context={"request": request}
    )

    if serializer.is_valid():

        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def address_detail(request, pk):

    try:
        address = Address.objects.get(id=pk, user=request.user)
    except Address.DoesNotExist:
        return Response(
            {"message": "Address not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    if request.method == "PUT":

        serializer = AddressSerializer(
            address,
            data=request.data,
            partial=True,
            context={"request": request}
        )

        if serializer.is_valid():

            serializer.save()
            return Response(serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    address.delete()

    return Response(
        {"message": "Address deleted successfully"},
        status=status.HTTP_200_OK
    )
    
    
    
# New catogary view  
from django.db.models import Prefetch

@api_view(["GET"])
def home_categories(request):

    categories = Category.objects.filter(
        is_active=True
    ).prefetch_related(
        Prefetch(
            "subcategories",
            queryset=SubCategory.objects.filter(is_active=True)
        )
    )

    serializer = HomeCategorySerializer(
        categories,
        many=True
    )

    return Response(serializer.data)



@api_view(["GET"])
@permission_classes([IsSuperAdmin])
def admin_order_details(request, id):

    if not request.user.is_staff:

        return Response(
            {
                "message": "Permission Denied"
            },
            status=403
        )

    try:

        order = Order.objects.select_related(
            "user",
            "address"
        ).prefetch_related(
            "items__product",
            "items__color",
            "items__size"
        ).get(
            id=id
        )

    except Order.DoesNotExist:

        return Response(
            {
                "message": "Order not found"
            },
            status=404
        )

    serializer = OrderSerializer(order)

    return Response(serializer.data)


@api_view(["PUT"])
@permission_classes([IsSuperAdmin])
def update_order_status(request, id):

    if not request.user.is_staff:

        return Response(
            {
                "message": "Permission Denied"
            },
            status=403
        )

    try:

        order = Order.objects.get(
            id=id
        )

    except Order.DoesNotExist:

        return Response(
            {
                "message": "Order not found"
            },
            status=404
        )

    status_value = request.data.get("status")

    if not status_value:

        return Response(
            {
                "message": "Status is required"
            },
            status=400
        )

    allowed_status = [

        "Pending",
        "Processing",
        "Shipped",
        "Delivered",
        "Cancelled"

    ]

    if status_value not in allowed_status:

        return Response(
            {
                "message": "Invalid Status"
            },
            status=400
        )

    order.status = status_value

    order.save()

    return Response(
        {
            "message": "Order status updated successfully",
            "status": order.status
        }
    )
    
from django.db.models import Count

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wishlist_products(request):

    if not request.user.is_staff:

        return Response(
            {
                "error": "Unauthorized"
            },
            status=403
        )

    variants = ProductVariant.objects.annotate(

        wishlist_count=Count(
            "wishlist",
            distinct=True
        )

    ).filter(

        wishlist_count__gt=0

    ).select_related(

        "product",
        "product__category",
        "color"

    ).order_by(

        "-wishlist_count"

    )

    data = []

    for variant in variants:

        image = None

        product_image = variant.images.filter(
            is_primary=True
        ).first()

        if not product_image:

            product_image = variant.images.first()

        if product_image:

            image = request.build_absolute_uri(
                product_image.image.url
            )

        data.append({

            "variant_id": variant.id,

            "product_id": variant.product.id,

            "product_name": variant.product.name,

            "color": (
                variant.color.name
                if variant.color
                else None
            ),

            "image": image,

            "category": (
                variant.product.category.name
                if variant.product.category
                else None
            ),

            "wishlist_count": variant.wishlist_count

        })

    return Response(data)


@api_view(["GET"])
def get_hero_banners(request):

    banners = HeroBanner.objects.filter(
        is_active=True
    ).order_by(
        "display_order"
    )

    serializer = HeroBannerSerializer(
        banners,
        many=True
    )

    return Response(
        serializer.data
    )

@api_view(["GET"])
def get_promo_banners(request):
    banners = PromoBanner.objects.filter(is_active=True)
    serializer = PromoBannerSerializer(banners, many=True)
    return Response(serializer.data)

@api_view(["GET"])
def get_hero_side_banner(request):
    banner = HeroSideBanner.objects.filter(is_active=True).first()
    if banner:
        serializer = HeroSideBannerSerializer(banner)
        return Response(serializer.data)
    return Response({})

from rest_framework import viewsets, filters
from django.utils import timezone

class CouponViewSet(viewsets.ModelViewSet):
    queryset = Coupon.objects.select_related("category").prefetch_related("products").all()
    serializer_class = CouponSerializer
    # Keep the legacy route restricted to the same Super Admin boundary as
    # the dashboard management API.
    permission_classes = [IsSuperAdmin]
    filter_backends = [filters.SearchFilter]
    search_fields = ['code', 'products__name', 'category__name']

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def validate_coupon(request):
    code = str(request.data.get('code') or '').strip()
    product_id = request.data.get('product_id')

    if not code or not product_id:
        return Response({'message': 'Coupon code and product_id are required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return Response({'message': 'Invalid product_id'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        product = Product.objects.get(id=product_id, is_active=True)
    except Product.DoesNotExist:
        return Response({'message': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        # Lock and reload the authoritative row so an admin status change
        # cannot race this application attempt.
        coupon = Coupon.objects.select_for_update().get(code=code)
    except Coupon.DoesNotExist:
        return Response({'message': 'Invalid coupon code'}, status=status.HTTP_404_NOT_FOUND)
        
    now = timezone.now()
    if not coupon.is_active or not (coupon.start_date <= now <= coupon.end_date):
        detail = 'This coupon is no longer active.'
        return Response(
            {
                'error_code': 'COUPON_INACTIVE',
                'detail': detail,
                'coupon_code': coupon.code,
                # Keep the existing frontend message contract as well.
                'message': 'Coupon is expired or inactive',
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not coupon_applies_to_product(coupon, product):
        return Response({'message': 'Coupon is not applicable for this product'}, status=status.HTTP_400_BAD_REQUEST)

    already_applied = (
        CouponUsage.objects.filter(
            coupon=coupon,
            user=request.user,
            product=product,
        ).exists()
        or CouponApplication.objects.filter(
            coupon=coupon,
            user=request.user,
            product=product,
        ).exists()
        or SavedCoupon.objects.filter(
            coupon=coupon,
            user=request.user,
            product=product,
        ).exists()
        or Cart.objects.filter(
            user=request.user,
            variant__product=product,
            coupon=coupon,
        ).exists()
    )
    if already_applied:
        return Response(
            {
                'message': 'You have already applied this coupon to this product.',
                'error_code': 'COUPON_ALREADY_APPLIED',
                'coupon_id': coupon.id,
                'product_id': product.id,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # The unique constraint is the backend guard for concurrent requests.
    # Convert a race into the same warning instead of leaking an IntegrityError.
    try:
        with transaction.atomic():
            CouponApplication.objects.create(
                coupon=coupon,
                user=request.user,
                product=product,
            )
    except IntegrityError:
        return Response(
            {
                'message': 'You have already applied this coupon to this product.',
                'error_code': 'COUPON_ALREADY_APPLIED',
                'coupon_id': coupon.id,
                'product_id': product.id,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
        
    SavedCoupon.objects.update_or_create(
        user=request.user,
        product=product,
        defaults={'coupon': coupon}
    )
    
    Cart.objects.filter(
        user=request.user,
        variant__product=product
    ).update(coupon=coupon)

    if coupon.applicability_type == Coupon.APPLICABILITY_CATEGORY:
        # Apply the category coupon to other currently-carted eligible lines
        # while leaving an explicitly selected different coupon untouched.
        Cart.objects.filter(
            user=request.user,
            variant__product__category_id=product.category_id,
            coupon__isnull=True,
        ).update(coupon=coupon)
        
    return Response({
        'message': 'Coupon applied successfully',
        'discount_percentage': coupon.discount_percentage,
        'fixed_amount': coupon.fixed_amount,
        'discount_type': coupon.discount_type,
        'applicability_type': coupon.applicability_type,
        'coupon_id': coupon.id
    }, status=status.HTTP_200_OK)
