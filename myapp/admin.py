import nested_admin
from django.contrib import admin
from django import forms
from .models import *

admin.site.site_header = "eehook Admin"
admin.site.site_title = "eehook"
admin.site.index_title = "Welcome To eehook Admin Dashboard"

@admin.register(User)
class UserAdmin(admin.ModelAdmin):

    list_display = (
        "email",
        "is_staff",
        "is_active"
    )

    search_fields = ( "email",)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "is_active",
        "created_at"
    )

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
    list_display = ("name", "unit_type", "order")
    list_filter = ("unit_type",)

@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)

class ProductImageInline( nested_admin.NestedTabularInline ):

    model = ProductImage
    extra = 1

class ProductVariantSizeInline( nested_admin.NestedTabularInline):

    model = ProductVariantUnit
    extra = 1
    fields = ('unit_type', 'unit', 'price', 'stock')

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
        emi_starting_price = cleaned_data.get('emi_starting_price')

        if emi_starting_price is not None:
            if hasattr(self, 'request'):
                post_data = self.request.POST
                import re
                price_keys = [k for k in post_data.keys() if re.search(r'variants-\d+-sizes-\d+-price', k)]
                for k in price_keys:
                    try:
                        price_val = float(post_data[k])
                        if float(emi_starting_price) > price_val:
                            self.add_error('emi_starting_price', 'EMI starting price cannot be greater than the product price.')
                            break
                    except (ValueError, TypeError):
                        continue
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

        ("EMI Details",
            {"fields": ("emi_available", "emi_starting_price")}
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
admin.site.register(Order)
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
    list_display = ('code', 'copy_code_button', 'discount_percentage', 'start_date', 'end_date', 'is_active')
    search_fields = ('code', 'products__name')
    autocomplete_fields = ('products',)
    list_filter = ('is_active', 'start_date', 'end_date')

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