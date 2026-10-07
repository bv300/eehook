import nested_admin
from django.contrib import admin
from django import forms
from django.db import transaction
from .models import *

admin.site.site_header = "Order Dashboard"
admin.site.site_title = "Order Dashboard"
admin.site.index_title = "Order Dashboard"

@admin.register(User)
class UserAdmin(admin.ModelAdmin):

    list_display = (
        "email",
        "role",
        "is_staff",
        "is_active"
    )

    search_fields = ( "email",)
    list_filter = ("role", "is_active")

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "is_active",
        "created_at"
    )

    search_fields = ("name",)

@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "category",
        "is_active"
    )

    list_filter = ( "category", )

@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):

    list_display = (
        "title",
        "discount_percentage",
        "is_active"
    )
    
@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):

    list_display = ("name","code")

@admin.register(UnitType)
class UnitTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)

@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("name", "unit_type")
    list_filter = ("unit_type",)

@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)

class ProductImageInline( nested_admin.NestedTabularInline ):

    model = ProductImage
    extra = 1
    fields = ('image', 'position', 'is_primary')
    ordering = ('position', 'id')

class ProductVariantSizeInline( nested_admin.NestedTabularInline):

    model = ProductVariantUnit
    extra = 1
    fields = ('unit_type', 'unit', 'sku', 'price', 'stock')

class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        exclude = ('regions',)
        widgets = {
            'price_type': forms.RadioSelect(choices=ProductVariant.PRICE_TYPE_CHOICES),
        }

class ProductVariantInline( nested_admin.NestedStackedInline ):

    model = ProductVariant
    form = ProductVariantForm
    extra = 1
    inlines = [
        ProductImageInline,
        ProductVariantSizeInline
    ]

class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        subcategory = cleaned_data.get('subcategory')
        if category and subcategory and subcategory.category_id != category.id:
            self.add_error(
                'subcategory',
                'The subcategory must belong to the selected category.',
            )

        return cleaned_data

@admin.register(Product)
class ProductAdmin(nested_admin.NestedModelAdmin):
    form = ProductAdminForm
    
    class Media:
        js = ('js/price_toggle.js',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.request = request
        return form

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        # Product, variants, sizes, and images submitted through the nested
        # admin form must commit or roll back together.
        with transaction.atomic():
            return super().changeform_view(request, object_id, form_url, extra_context)

    list_display = (
        "name",
        "category",
        "subcategory",
        "is_active"
    )

    list_filter = (
        "category",
        "subcategory",
        "is_active"
    )

    search_fields = ("name",)

    fieldsets = (
        ("Product Information",
            {    "fields": ("name", "description", "key_features")}
        ),

        ( "Category Details",
            { "fields": ( "category","subcategory")}
        ),

        ("Offer Details",
            {"fields": ( "offer", ) }
        ),

        ("Sales & Delivery Info",
            {"fields": ("seller_name", "shipping_fee", "estimated_delivery_time", "warranty_info")}
        ),

        ("Promotional & Social",
            {"fields": ("current_viewers_count", "promotional_banner_image", "promotional_banner_link")}
        ),

        ("Status",
            {"fields": ("is_active",)}
        ),
        )

    inlines = [ProductVariantInline]

@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "get_product",
        "variant",
        "variant_unit",
        "created_at"
    )

    def get_product(self, obj):
        return obj.variant.product.name

    get_product.short_description = "Product"

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "variant_unit",
        "quantity"
    )

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):

    list_display = (
        "full_name",
        "city",
        "postal_code",
        "country",
        "phone"
    )

admin.site.register(UserProfile)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):

    list_display = (
        "order_number",
        "customer_email",
        "total_amount",
        "payment_status",
        "status",
        "created_at",
    )
    list_filter = ("status", "payment_status", "created_at")
    search_fields = ("user__email", "user__first_name", "stripe_session_id")
    ordering = ("-created_at",)

    @admin.display(description="Order")
    def order_number(self, obj):
        return str(obj)

    @admin.display(description="Customer")
    def customer_email(self, obj):
        return obj.user.email

admin.site.register(OrderItem)


@admin.register(HeroBanner)
class HeroBannerAdmin(admin.ModelAdmin):

    list_display = (
        "title",
        "display_order",
        "is_active",
    )

    list_filter = (
        "is_active",
    )

    ordering = (
        "display_order",
    )

    search_fields = (
        "title",
        "subtitle",
    )

@admin.register(PromoBanner)
class PromoBannerAdmin(admin.ModelAdmin):
    list_display = ("id", "is_active", "link")
    list_filter = ("is_active",)
    search_fields = ("link",)

@admin.register(HeroSideBanner)
class HeroSideBannerAdmin(admin.ModelAdmin):
    list_display = ("id", "is_active", "link")
    list_filter = ("is_active",)

from django.utils.html import format_html

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    class CouponAdminForm(forms.ModelForm):
        class Meta:
            model = Coupon
            fields = "__all__"

        def clean(self):
            cleaned = super().clean()
            applicability = cleaned.get("applicability_type")
            products = cleaned.get("products")
            category = cleaned.get("category")
            if applicability == Coupon.APPLICABILITY_PRODUCT and not products and not self.instance.pk:
                self.add_error("products", "Select at least one product for a product-wise coupon.")
            if applicability == Coupon.APPLICABILITY_CATEGORY:
                if not category:
                    self.add_error("category", "A category is required for a category-wise coupon.")
                if products:
                    self.add_error("products", "Category-wise coupons must not contain product targets.")
            return cleaned

    form = CouponAdminForm
    list_display = (
        "code",
        "applicability_type",
        "coupon_target",
        "discount_type",
        "discount_value",
        "start_date",
        "end_date",
        "is_active",
    )
    search_fields = ('code', 'products__name', 'category__name')
    autocomplete_fields = ('products', 'category')
    list_filter = ('applicability_type', 'discount_type', 'is_active', 'start_date', 'end_date')

    def coupon_target(self, obj):
        if obj.applicability_type == Coupon.APPLICABILITY_CATEGORY:
            return obj.category.name if obj.category else "-"
        return ", ".join(obj.products.values_list("name", flat=True)[:3]) or "All products (legacy)"

    coupon_target.short_description = "Target"

    def discount_value(self, obj):
        if obj.discount_type == Coupon.DISCOUNT_FIXED:
            return obj.fixed_amount
        return f"{obj.discount_percentage}%"

    discount_value.short_description = "Discount"

    def copy_code_button(self, obj):
        if obj.code:
            return format_html(
                '<button type="button" onclick="navigator.clipboard.writeText(\'{}\'); alert(\'Copied: {}\')" style="cursor: pointer; padding: 2px 6px; background-color: #417690; color: white; border: none; border-radius: 3px;">Copy</button>',
                obj.code, obj.code
            )
        return "-"
    copy_code_button.short_description = "Copy Code"

@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'coupon', 'product', 'used_at')
    search_fields = ('user__email', 'coupon__code', 'product__name')
    list_filter = ('used_at',)

@admin.register(CouponApplication)
class CouponApplicationAdmin(admin.ModelAdmin):
    list_display = ('user', 'coupon', 'product', 'applied_at')
    search_fields = ('user__email', 'coupon__code', 'product__name')
    list_filter = ('applied_at',)
