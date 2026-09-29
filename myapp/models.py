from django.db import models
from django.contrib.auth.models import ( AbstractUser, BaseUserManager)


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):

        if not email:
            raise ValueError("Email is required")

        email = self.normalize_email(email)

        user = self.model( email=email, **extra_fields )

        user.set_password(password)

        user.save(using=self._db)

        return user

    def create_superuser(self, email, password=None, **extra_fields):

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        return self.create_user(
            email,
            password,
            **extra_fields
        )

class User(AbstractUser):

    username = None
    email = models.EmailField( unique=True )

    USERNAME_FIELD = "email"

    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class Category(models.Model):

    name = models.CharField(max_length=100,unique=True)
    image = models.ImageField( upload_to="categories/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class SubCategory(models.Model):

    category = models.ForeignKey( Category, on_delete=models.CASCADE,related_name="subcategories")
    name = models.CharField( max_length=100)
    image = models.ImageField( upload_to="subcategories/", blank=True, null=True )
    is_active = models.BooleanField( default=True )
    created_at = models.DateTimeField( auto_now_add=True )

    def __str__(self):
        return self.name

class Offer(models.Model):

    title = models.CharField(max_length=200 )
    description = models.TextField()
    image = models.ImageField( upload_to="offers/")
    discount_percentage = models.PositiveIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField( default=True)
    created_at = models.DateTimeField( auto_now_add=True)

    def __str__(self):
        return self.title
    
class Color(models.Model):

    name = models.CharField(max_length=50,unique=True)
    code = models.CharField(max_length=7)

    def __str__(self):
        return self.name
    
class UnitType(models.Model):

    name = models.CharField(max_length=50, unique=True, help_text="e.g. Weight (kg), Clothing Unit (S/M/L), Memory (GB)")

    def __str__(self):
        return self.name

class Unit(models.Model):

    unit_type = models.ForeignKey(UnitType, on_delete=models.CASCADE, null=True, blank=True, related_name="units")
    name = models.CharField( max_length=20, unique=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        
    def __str__(self):
        if self.unit_type:
            return f"{self.name} ({self.unit_type.name})"
        return self.name

class Region(models.Model):

    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name

class Product(models.Model):

    category = models.ForeignKey(Category,on_delete=models.CASCADE)
    subcategory = models.ForeignKey( SubCategory,on_delete=models.CASCADE)
    offer = models.ForeignKey( Offer, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField( max_length=200 )
    description = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField( auto_now_add=True )
    updated_at = models.DateTimeField( auto_now=True )
    key_features = models.TextField(blank=True, help_text="Enter key features separated by newlines")
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    estimated_delivery_time = models.CharField(max_length=100, blank=True, null=True)
    seller_name = models.CharField(max_length=200, blank=True, null=True)
    warranty_info = models.CharField(max_length=200, blank=True, null=True)
    emi_available = models.BooleanField(default=False)
    emi_starting_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    current_viewers_count = models.PositiveIntegerField(default=0)
    promotional_banner_image = models.ImageField(upload_to="promotional_banners/", blank=True, null=True)
    promotional_banner_link = models.URLField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        from django.db.models import Min
        if self.emi_starting_price is not None:
            if self.pk:
                min_price = ProductVariantUnit.objects.filter(variant__product=self).aggregate(Min('price'))['price__min']
                if min_price is not None and self.emi_starting_price > min_price:
                    raise ValidationError({'emi_starting_price': 'EMI starting price cannot be greater than the product price.'})

class ProductView(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="unique_views")
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['product', 'user']),
            models.Index(fields=['product', 'ip_address']),
        ]


class ProductVariant(models.Model):
    PRICE_TYPE_CHOICES = (
        ("single", "Single Price"),
        ("multiple", "Multiple Price"),
    )
    product = models.ForeignKey( Product, on_delete=models.CASCADE, related_name="variants" )
    color = models.ForeignKey(Color,on_delete=models.CASCADE, null=True, blank=True )
    regions = models.ManyToManyField(Region, blank=True)
    
    price_type = models.CharField(max_length=20, choices=PRICE_TYPE_CHOICES, default="single")
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="For single price")

    class Meta:
        unique_together = (
            "product",
            "color"
        )
    def __str__(self):
        parts = [self.product.name]
        if self.color:
            parts.append(self.color.name)
        if self.pk:
            try:
                region_names = [r.name for r in self.regions.all()]
                if region_names:
                    parts.append(", ".join(region_names))
            except ValueError:
                pass
        return " - ".join(parts)

class ProductVariantUnit(models.Model):

    variant = models.ForeignKey(ProductVariant,on_delete=models.CASCADE, related_name="sizes" )
    unit_type = models.ForeignKey(UnitType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Unit (e.g. GB/KG)")
    unit = models.ForeignKey( Unit,  on_delete=models.CASCADE, null=True, blank=True, verbose_name="Quantity (e.g. 128, 1)")
    price = models.DecimalField( max_digits=10,decimal_places=2)
    stock = models.PositiveIntegerField( default=0)

    class Meta:
        unique_together = (
            "variant",
            "unit"
        )
    def __str__(self):
        if self.unit:
            return (
                f"{self.variant} - "
                f"{self.unit.name}"
            )

        return str(self.variant)
    
class ProductImage(models.Model):

    variant = models.ForeignKey( ProductVariant, on_delete=models.CASCADE,related_name="images", null=True, blank=True)
    image = models.ImageField( upload_to="products/")
    is_primary = models.BooleanField( default=False)

    def __str__(self):
        return (
            f"{self.variant.product.name} - "
      
        )

class Wishlist(models.Model):

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    variant = models.ForeignKey(
    ProductVariant,
    on_delete=models.CASCADE,
    related_name="wishlist",
    null=True,
    blank=True
    )

    variant_unit = models.ForeignKey(
    ProductVariantUnit,
    on_delete=models.CASCADE,
    related_name="wishlist",
    null=True,
    blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        unique_together = (
            "user",
            "variant",
            "variant_unit"
        )

    def __str__(self):

        color = (
            self.variant.color.name
            if self.variant.color
            else ""
        )

        unit = (
            self.variant_unit.unit.name
            if self.variant_unit.unit
            else ""
        )

        return (
            f"{self.user.email} - "
            f"{self.variant.product.name} - "
            f"{color} - "
            f"{unit}"
        )
        
class Cart(models.Model):

    user = models.ForeignKey( User, on_delete=models.CASCADE )
    variant = models.ForeignKey( ProductVariant, on_delete=models.CASCADE )
    variant_unit = models.ForeignKey( ProductVariantUnit,on_delete=models.CASCADE, null=True, blank=True)
    quantity = models.PositiveIntegerField( default=1)
    created_at = models.DateTimeField( auto_now_add=True )

    class Meta:
        unique_together = (
            "user",
            "variant",
            "variant_unit"
        )

    def __str__(self):
        return (
            f"{self.user.email} - "
            f"{self.variant.product.name} - "
            f"{self.variant.color.name} - "
            f"{self.variant_unit.unit.name}"
        )

class Address(models.Model):

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="addresses"
        ,null=True
    )

    full_name = models.CharField(max_length=100, blank=True,null=True)
    phone = models.CharField(max_length=15, blank=True,null=True)
    address_line = models.TextField(blank=True,null=True)
    city = models.CharField(max_length=100, blank=True,null=True)
    postal_code = models.CharField(max_length=20, blank=True,null=True)
    country = models.CharField(max_length=100, default="New Zealand")
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True,null=True)

    def __str__(self):
        return f"{self.full_name} - {self.city}"
    
class UserProfile(models.Model):

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
        , related_name="profile"
        ,null=True
    )

    phone = models.CharField(max_length=15, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(blank=True, null=True)

class Order(models.Model):

    STATUS_CHOICES = (
        ("Pending", "Pending"),
        ("Processing", "Processing"),
        ("Shipped", "Shipped"),
        ("Delivered", "Delivered"),
        ("Cancelled", "Cancelled"),
    )

    PAYMENT_STATUS_CHOICES = (
        ("Pending", "Pending"),
        ("Paid", "Paid"),
        ("Failed", "Failed"),
        ("Refunded", "Refunded"),
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True
    )

    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    shipping_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default="Pending"
    )

    stripe_session_id = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="Pending"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"ORD-{self.id:06d}"

class OrderItem(models.Model):

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE
    )

    color = models.ForeignKey(
        Color,
        on_delete=models.SET_NULL,
        null=True
    )
    

    unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True
    )

    quantity = models.PositiveIntegerField()

    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )
    
    variant_unit = models.ForeignKey(
        ProductVariantUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    ) 


    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    def __str__(self):
        return self.product.name
    
class HeroBanner(models.Model):

    subtitle = models.CharField(max_length=100)

    title = models.CharField(max_length=200)

    description = models.TextField()

    image = models.ImageField(upload_to="hero_banners/")

    button_text = models.CharField(
        max_length=50,
        default="Explore Collection"
    )

    display_order = models.PositiveIntegerField(default=1)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order"]

    def __str__(self):
        return self.title

class PromoBanner(models.Model):

    image = models.ImageField(upload_to="promo_banners/", help_text='The promotional banner image', null=True, blank=True)
    link = models.URLField(max_length=500, blank=True, null=True, help_text='A URL where the user should be redirected if they click the banner')
    is_active = models.BooleanField(default=True, help_text='To easily turn individual banners on or off')

    def __str__(self):
        return f"Promo Banner {self.id}"

class HeroSideBanner(models.Model):

    image = models.ImageField(upload_to="hero_side_banners/", help_text='The actual image file')
    link = models.URLField(max_length=500, blank=True, null=True, help_text='A URL where the user should be redirected if they click the banner')
    is_active = models.BooleanField(default=True, help_text='Only one banner should be active at a time')

    def __str__(self):
        return f"Hero Side Banner {self.id}"

class Coupon(models.Model):

    code = models.CharField(max_length=50, unique=True, blank=True, help_text="Leave blank to auto-generate")
    products = models.ManyToManyField(Product, blank=True, related_name="coupons", help_text="Select specific products. Leave blank to apply to ALL products.")
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if not self.code:
            import string, random
            while True:
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                if not Coupon.objects.filter(code=code).exists():
                    self.code = code
                    break
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        from django.utils import timezone
        now = timezone.now()
        return self.is_active and self.start_date <= now <= self.end_date

class CouponUsage(models.Model):

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="usages")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="used_coupons")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="coupon_usages", null=True)
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('coupon', 'user', 'product')

    def __str__(self):
        return f"{self.user.email} - {self.coupon.code} on {self.product}"