from django.test import TestCase

# Create your tests here.

# Keep the security regression suite discoverable by Django's default runner.
from rest_framework.test import APIClient
from django.test import override_settings
from django.core.cache import cache
from django.utils import timezone

from decimal import Decimal
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from PIL import Image

from .admin import ProductAdminForm, ProductVariantForm
from .models import (
    AdminAuditLog,
    Category,
    Color,
    Coupon,
    CouponApplication,
    CouponUsage,
    Cart,
    Product,
    ProductImage,
    ProductVariant,
    ProductVariantUnit,
    SubCategory,
    Unit,
    UnitType,
    User,
)


@override_settings(SECURE_SSL_REDIRECT=False)
class SecurityBoundaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def test_cart_requires_authentication(self):
        response = self.client.get("/cart/")
        self.assertIn(response.status_code, (401, 403))

    def test_order_detail_requires_authentication(self):
        response = self.client.get("/order-details/1/")
        self.assertIn(response.status_code, (401, 403))

    def test_admin_dashboard_is_not_public(self):
        response = self.client.get("/admin-dashboard-cards/")
        self.assertIn(response.status_code, (401, 403))

    def test_invalid_catalog_filter_is_rejected(self):
        response = self.client.get("/products/?category=1%20OR%201%3D1")
        self.assertEqual(response.status_code, 400)

    def test_untrusted_ordering_is_rejected(self):
        response = self.client.get("/products/?sort=price%27%20DESC")
        self.assertEqual(response.status_code, 400)

    def test_password_reset_does_not_disclose_account_existence(self):
        response = self.client.post("/forgot-password/", {"email": "not-an-email"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("message", response.data)

    @override_settings(
        GLOBAL_RATE_LIMIT=1000,
        ADMIN_LOGIN_MAX_ATTEMPTS=2,
        ADMIN_LOGIN_WINDOW=900,
    )
    def test_admin_login_is_locked_after_repeated_failures(self):
        for _ in range(2):
            response = self.client.post(
                "/admin/login/",
                {"username": "admin@example.com", "password": "wrong"},
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/admin/login/",
            {"username": "admin@example.com", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)


    def test_login_types_keep_customer_and_super_admin_flows_separate(self):
        customer = User.objects.create_user(
            email="customer-login@example.invalid",
            password="CustomerPass123!",
            role="Customer",
        )
        super_admin = User.objects.create_superuser(
            email="super-admin-login@example.invalid",
            password="SuperAdminPass123!",
        )

        customer_response = self.client.post(
            "/login/",
            {"email": customer.email, "password": "CustomerPass123!", "login_type": "customer"},
            format="json",
        )
        self.assertEqual(customer_response.status_code, 200)

        admin_through_customer_response = self.client.post(
            "/login/",
            {"email": super_admin.email, "password": "SuperAdminPass123!", "login_type": "customer"},
            format="json",
        )
        self.assertEqual(admin_through_customer_response.status_code, 403)

        customer_through_admin_response = self.client.post(
            "/login/",
            {"email": customer.email, "password": "CustomerPass123!", "login_type": "super_admin"},
            format="json",
        )
        self.assertEqual(customer_through_admin_response.status_code, 403)

        super_admin_response = self.client.post(
            "/login/",
            {"email": super_admin.email, "password": "SuperAdminPass123!", "login_type": "super_admin"},
            format="json",
        )
        self.assertEqual(super_admin_response.status_code, 200)

    def test_registration_rejects_weak_passwords(self):
        response = self.client.post(
            "/register/",
            {
                "first_name": "New",
                "email": "weak-password@example.invalid",
                "password": "password",
                "confirm_password": "password",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST_USER="noreply@example.invalid",
        SITE_URL="http://localhost:5173",
    )
    def test_forgot_password_sends_frontend_reset_link_and_accepts_strong_password(self):
        user = User.objects.create_user(
            email="reset-flow@example.invalid",
            password="ExistingPass123!",
            role="Customer",
        )

        forgot_response = self.client.post(
            "/forgot-password/",
            {"email": user.email},
            format="json",
        )
        self.assertEqual(forgot_response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        reset_link = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if "/reset-password/" in line
        )
        reset_path = reset_link.split("http://localhost:5173", 1)[1]

        reset_response = self.client.post(
            f"{reset_path}/",
            {
                "password": "NewStrongPass123!",
                "confirm_password": "NewStrongPass123!",
            },
            format="json",
        )
        self.assertEqual(reset_response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewStrongPass123!"))

        weak_reset_response = self.client.post(
            f"{reset_path}/",
            {
                "password": "weakpass",
                "confirm_password": "weakpass",
            },
            format="json",
        )
        self.assertEqual(weak_reset_response.status_code, 400)
        self.assertIn("password", weak_reset_response.data)

    @override_settings(GLOBAL_RATE_LIMIT=2, GLOBAL_RATE_WINDOW=60)
    def test_global_request_flood_returns_429(self):
        self.client.get("/products/")
        self.client.get("/products/")
        response = self.client.get("/products/")
        self.assertEqual(response.status_code, 429)


class CouponApplicationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="coupon-user@example.invalid",
            password="CouponPass123!",
            role="Customer",
        )
        self.other_user = User.objects.create_user(
            email="other-coupon-user@example.invalid",
            password="CouponPass123!",
            role="Customer",
        )
        category = Category.objects.create(
            name="Coupon Test Category",
            image=SimpleUploadedFile("coupon-category.jpg", b"category"),
        )
        subcategory = SubCategory.objects.create(
            category=category,
            name="Coupon Test Subcategory",
        )
        self.product = Product.objects.create(
            category=category,
            subcategory=subcategory,
            name="Coupon Test Product",
            description="A product for coupon validation tests.",
        )
        self.other_product = Product.objects.create(
            category=category,
            subcategory=subcategory,
            name="Other Coupon Test Product",
            description="Another product for coupon validation tests.",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            price_type="single",
            price=Decimal("10.00"),
            stock=5,
        )
        self.other_variant = ProductVariant.objects.create(
            product=self.other_product,
            price_type="single",
            price=Decimal("12.00"),
            stock=5,
        )
        now = timezone.now()
        self.coupon = Coupon.objects.create(
            code="ONCEONLY",
            discount_percentage=10,
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.user)

    def validate(self, product):
        return self.client.post(
            "/validate-coupon/",
            {"code": self.coupon.code, "product_id": product.id},
            format="json",
        )

    def test_coupon_application_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.validate(self.product)
        self.assertIn(response.status_code, (401, 403))

    def test_coupon_application_is_once_per_user_coupon_and_product(self):
        first = self.validate(self.product)
        self.assertEqual(first.status_code, 200, first.data)

        second = self.validate(self.product)
        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(second.data["error_code"], "COUPON_ALREADY_APPLIED")
        self.assertEqual(
            CouponApplication.objects.filter(
                user=self.user,
                coupon=self.coupon,
                product=self.product,
            ).count(),
            1,
        )

        other_product = self.validate(self.other_product)
        self.assertEqual(other_product.status_code, 200, other_product.data)
        added = self.client.post(
            "/cart/add/",
            {"variant": self.other_variant.id, "quantity": 1},
            format="json",
        )
        self.assertEqual(added.status_code, 200, added.data)
        self.assertEqual(
            Cart.objects.get(user=self.user, variant=self.other_variant).coupon_id,
            self.coupon.id,
        )

        self.client.force_authenticate(user=self.other_user)
        other_user = self.validate(self.product)
        self.assertEqual(other_user.status_code, 200, other_user.data)

    def test_variant_independence_is_enforced_by_product_id(self):
        self.assertEqual(self.validate(self.product).status_code, 200)
        self.assertEqual(self.validate(self.product).status_code, 400)

    def test_coupon_usage_on_one_product_does_not_block_another_product(self):
        CouponUsage.objects.create(
            user=self.user,
            coupon=self.coupon,
            product=self.product,
        )

        used_product = self.validate(self.product)
        self.assertEqual(used_product.status_code, 400)

        other_product = self.validate(self.other_product)
        self.assertEqual(other_product.status_code, 200, other_product.data)

    def test_applied_coupon_is_carried_to_cart_without_becoming_reusable(self):
        self.assertEqual(self.validate(self.product).status_code, 200)

        added = self.client.post(
            "/cart/add/",
            {"variant": self.variant.id, "quantity": 1},
            format="json",
        )
        self.assertEqual(added.status_code, 200, added.data)
        self.assertEqual(
            Cart.objects.get(user=self.user, variant=self.variant).coupon_id,
            self.coupon.id,
        )
        self.assertEqual(self.validate(self.product).status_code, 400)


class ProductWorkflowRegressionTests(TestCase):
    """Regression coverage for the product-management audit findings."""

    def setUp(self):
        self.media_dir = TemporaryDirectory(prefix="product-tests-")
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_dir.cleanup)

        self.admin = User.objects.create_superuser(
            email="product-admin@example.invalid",
            password="AuditPass123!",
        )
        self.category = Category.objects.create(
            name="Test Electronics",
            image=self.image_file("category.jpg", (20, 20, 20)),
        )
        self.other_category = Category.objects.create(
            name="Test Footwear",
            image=self.image_file("other-category.jpg", (30, 30, 30)),
        )
        self.subcategory = SubCategory.objects.create(
            category=self.category,
            name="Test Devices",
        )
        self.other_subcategory = SubCategory.objects.create(
            category=self.other_category,
            name="Test Shoes",
        )
        self.black = Color.objects.create(name="Test Black", code="#000000")
        self.white = Color.objects.create(name="Test White", code="#FFFFFF")
        self.weight = UnitType.objects.create(name="Test Weight")
        self.storage = UnitType.objects.create(name="Test Storage")
        self.kg = Unit.objects.create(unit_type=self.weight, name="Test KG")
        self.gb = Unit.objects.create(unit_type=self.storage, name="Test GB")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    @staticmethod
    def image_file(name, color):
        stream = BytesIO()
        Image.new("RGB", (24, 24), color).save(stream, format="JPEG")
        return SimpleUploadedFile(name, stream.getvalue(), content_type="image/jpeg")

    def create_product(self, name="Test Product", category=None, subcategory=None):
        return Product.objects.create(
            category=category or self.category,
            subcategory=subcategory or self.subcategory,
            name=name,
            description="A product used by the regression suite.",
        )

    def create_variant(self, product, **data):
        payload = {
            "product": product.id,
            "price_type": "single",
            "price": "10.00",
            "stock": 5,
            **data,
        }
        response = self.client.post(
            "/admin/manage/product-variants/", payload, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        return ProductVariant.objects.get(pk=response.data["id"])

    def test_admin_product_search_matches_name_and_skus_case_insensitively(self):
        name_match = self.create_product("iPhone-compatible charger")
        self.create_variant(name_match, sku="NAME-MATCH-SKU")

        variant_sku_match = self.create_product("Unrelated product")
        self.create_variant(variant_sku_match, sku="CAT50-IPHONEX")

        unit_sku_match = self.create_product("Another unrelated product")
        unit_variant = self.create_variant(
            unit_sku_match,
            price_type="multiple",
            price=None,
            stock=0,
        )
        ProductVariantUnit.objects.create(
            variant=unit_variant,
            unit_type=self.storage,
            unit=self.gb,
            sku="CAT50-IPHONEX-256GB",
            price="20.00",
            stock=1,
        )

        response = self.client.get(
            "/admin/manage/products/",
            {"search": "iphone", "page_size": 20},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
        self.assertEqual(
            {item["id"] for item in response.data["results"]},
            {name_match.id, variant_sku_match.id, unit_sku_match.id},
        )

        empty = self.client.get(
            "/admin/manage/products/",
            {"search": "does-not-exist", "page_size": 20},
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data["count"], 0)

    def test_variant_price_type_validation(self):
        product = self.create_product()

        with self.assertRaises(ValidationError):
            ProductVariant(
                product=product,
                price_type="single",
                price=None,
                stock=1,
            ).full_clean()

        missing_single_price = self.client.post(
            "/admin/manage/product-variants/",
            {"product": product.id, "price_type": "single", "stock": 5},
            format="json",
        )
        self.assertEqual(missing_single_price.status_code, 400)

        multiple_with_price = self.client.post(
            "/admin/manage/product-variants/",
            {
                "product": product.id,
                "price_type": "multiple",
                "price": "10.00",
                "stock": 0,
            },
            format="json",
        )
        self.assertEqual(multiple_with_price.status_code, 400)

        valid_single = self.create_variant(product)
        self.assertEqual(valid_single.price, Decimal("10.00"))
        duplicate_no_color = self.client.post(
            "/admin/manage/product-variants/",
            {
                "product": product.id,
                "price_type": "single",
                "price": "11.00",
                "stock": 1,
            },
            format="json",
        )
        self.assertEqual(duplicate_no_color.status_code, 400)
        valid_multiple = self.create_variant(
            self.create_product("Multiple-price product"),
            color=self.black.id,
            price_type="multiple",
            price=None,
            stock=0,
        )
        self.assertEqual(valid_multiple.price_type, "multiple")

        invalid_admin_form = ProductVariantForm(
            {
                "product": product.id,
                "price_type": "single",
                "price": "",
                "stock": "1",
            }
        )
        self.assertFalse(invalid_admin_form.is_valid())

    def test_category_subcategory_validation_is_shared(self):
        product = Product(
            category=self.category,
            subcategory=self.other_subcategory,
            name="Invalid relationship",
            description="Should fail",
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

        form = ProductAdminForm(
            {
                "category": self.category.id,
                "subcategory": self.other_subcategory.id,
                "name": "Invalid admin relationship",
                "description": "Should fail",
                "is_active": "on",
                "shipping_fee": "0.00",
                "current_viewers_count": "0",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("subcategory", form.errors)

        response = self.client.post(
            "/admin/manage/products/",
            {
                "category": self.category.id,
                "subcategory": self.other_subcategory.id,
                "name": "Invalid API relationship",
                "description": "Should fail",
                "emi_available": False,
                "emi_starting_price": "",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unit_type_relationship_and_duplicate_labels(self):
        clothing = UnitType.objects.create(name="Test Clothing")
        band = UnitType.objects.create(name="Test Band")
        clothing_m = Unit.objects.create(unit_type=clothing, name="M")
        band_m = Unit.objects.create(unit_type=band, name="M")
        self.assertNotEqual(clothing_m.id, band_m.id)

        product = self.create_product()
        variant = self.create_variant(product, price_type="multiple", price=None, stock=0)
        invalid = self.client.post(
            "/admin/manage/product-variant-units/",
            {
                "variant": variant.id,
                "unit_type": band.id,
                "unit": clothing_m.id,
                "price": "20.00",
                "stock": 3,
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)

        with self.assertRaises(ValidationError):
            ProductVariantUnit(
                variant=variant,
                unit_type=band,
                unit=clothing_m,
                price="20.00",
                stock=3,
            ).full_clean()

        valid = self.client.post(
            "/admin/manage/product-variant-units/",
            {
                "variant": variant.id,
                "unit_type": band.id,
                "unit": band_m.id,
                "sku": "TEST-BAND-M",
                "price": "20.00",
                "stock": 3,
            },
            format="json",
        )
        self.assertEqual(valid.status_code, 201, valid.data)

    def test_images_have_one_primary_and_stable_order(self):
        product = self.create_product()
        variant = self.create_variant(product, color=self.black.id)

        first = self.client.post(
            "/admin/manage/product-images/",
            {
                "variant": variant.id,
                "image": self.image_file("first.jpg", (1, 1, 1)),
                "position": 5,
                "is_primary": False,
            },
            format="multipart",
        )
        self.assertEqual(first.status_code, 201, first.data)

        second = self.client.post(
            "/admin/manage/product-images/",
            {
                "variant": variant.id,
                "image": self.image_file("second.jpg", (2, 2, 2)),
                "position": 1,
                "is_primary": False,
            },
            format="multipart",
        )
        self.assertEqual(second.status_code, 201, second.data)

        third = self.client.post(
            "/admin/manage/product-images/",
            {
                "variant": variant.id,
                "image": self.image_file("third.jpg", (3, 3, 3)),
                "position": 0,
                "is_primary": True,
            },
            format="multipart",
        )
        self.assertEqual(third.status_code, 201, third.data)
        self.assertEqual(
            ProductImage.objects.filter(variant=variant, is_primary=True).count(), 1
        )

        listed = self.client.get(
            "/admin/manage/product-images/", {"variant": variant.id}
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [item["position"] for item in listed.data["results"]], [0, 1, 5]
        )

        white_variant = self.create_variant(
            product,
            color=self.white.id,
            price_type="multiple",
            price=None,
            stock=0,
        )
        ProductVariantUnit.objects.create(
            variant=white_variant,
            unit_type=self.storage,
            unit=self.gb,
            sku="TEST-WHITE-GB",
            price="25.00",
            stock=2,
        )
        white_image = self.client.post(
            "/admin/manage/product-images/",
            {
                "variant": white_variant.id,
                "image": self.image_file("white.jpg", (5, 5, 5)),
                "position": 0,
                "is_primary": True,
            },
            format="multipart",
        )
        self.assertEqual(white_image.status_code, 201, white_image.data)
        detail = self.client.get(f"/product/{product.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.data["variants"]), 2)
        self.assertEqual(
            {item["color"]["name"] for item in detail.data["variants"]},
            {"Test Black", "Test White"},
        )
        self.assertEqual(
            next(
                item for item in detail.data["variants"]
                if item["color"]["name"] == "Test White"
            )["sizes"][0]["sku"],
            "TEST-WHITE-GB",
        )

        orphan = self.client.post(
            "/admin/manage/product-images/",
            {
                "variant": "",
                "image": self.image_file("orphan.jpg", (4, 4, 4)),
                "is_primary": True,
            },
            format="multipart",
        )
        self.assertEqual(orphan.status_code, 400)

        response = self.client.delete(
            f"/admin/manage/product-images/{third.data['id']}/"
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            ProductImage.objects.filter(variant=variant, is_primary=True).count(), 1
        )

    def test_public_sorting_and_pagination_use_catalog_prices(self):
        low = self.create_product("Low price")
        self.create_variant(low, price="10.00", stock=1)

        middle = self.create_product("Multiple price")
        middle_variant = self.create_variant(
            middle, color=self.black.id, price_type="multiple", price=None, stock=0
        )
        ProductVariantUnit.objects.create(
            variant=middle_variant,
            unit_type=self.storage,
            unit=self.gb,
            price="20.00",
            stock=1,
        )

        high = self.create_product("High price")
        self.create_variant(high, color=self.white.id, price="30.00", stock=1)

        response = self.client.get(
            "/products/", {"sort": "price_low", "page": 1, "page_size": 2}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
        prices = [str(item["starting_price"]) for item in response.data["results"]]
        self.assertEqual(prices, ["10.00", "20.00"])
        self.assertIsNotNone(response.data["next"])

        second_page = self.client.get(
            "/products/", {"sort": "price_low", "page": 2, "page_size": 2}
        )
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(str(second_page.data["results"][0]["starting_price"]), "30.00")

        search = self.client.get(
            "/search-products/", {"search": "Multiple", "page_size": 1}
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.data["results"][0]["name"], "Multiple price")

    def test_admin_create_rolls_back_when_audit_fails(self):
        before = Product.objects.count()
        with patch(
            "myapp.super_admin_api.AdminAuditLog.objects.create",
            side_effect=RuntimeError("audit failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/admin/manage/products/",
                    {
                        "category": self.category.id,
                        "subcategory": self.subcategory.id,
                        "name": "Should roll back",
                        "description": "Transaction test",
                        "emi_available": False,
                    },
                    format="json",
                )
        self.assertEqual(Product.objects.count(), before)

    def test_seeded_unit_catalog_contains_reusable_types(self):
        self.assertTrue(
            Unit.objects.filter(
                unit_type__name="Clothing Size", name="M"
            ).exists()
        )
        self.assertTrue(
            Unit.objects.filter(unit_type__name="Band Size", name="M").exists()
        )
        self.assertTrue(
            Unit.objects.filter(unit_type__name="Storage Capacity", name="1TB").exists()
        )
