from myapp.models import Cart, Wishlist
from myapp.serializers import CartSerializer, WishlistSerializer
import traceback

print("--- Testing Cart Serializer ---")
try:
    cart = Cart.objects.all()
    data = CartSerializer(cart, many=True).data
    print("Cart Serializer Success. Data length:", len(data))
except Exception as e:
    print("Cart Serializer Error:")
    traceback.print_exc()

print("\n--- Testing Wishlist Serializer ---")
try:
    wishlist = Wishlist.objects.all()
    data = WishlistSerializer(wishlist, many=True).data
    print("Wishlist Serializer Success. Data length:", len(data))
except Exception as e:
    print("Wishlist Serializer Error:")
    traceback.print_exc()
