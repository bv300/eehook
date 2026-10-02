from django.test import TestCase

# Create your tests here.

# Keep the security regression suite discoverable by Django's default runner.
from rest_framework.test import APIClient
from django.test import override_settings
from django.core.cache import cache


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

    @override_settings(GLOBAL_RATE_LIMIT=2, GLOBAL_RATE_WINDOW=60)
    def test_global_request_flood_returns_429(self):
        self.client.get("/products/")
        self.client.get("/products/")
        response = self.client.get("/products/")
        self.assertEqual(response.status_code, 429)
