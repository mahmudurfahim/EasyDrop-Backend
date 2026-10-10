from django.contrib.auth.models import AbstractUser
import re
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


def yt_ok(v):
    if not re.match(r"^https?://(www\.|m\.)?(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)[\w-]{6,}", v):
        raise ValidationError("Enter a direct YouTube link")


MAX_DESC_WORDS = 100


def desc_ok(v):  # product description: at most 100 words (and a sane length cap)
    n = len(str(v).split())
    if n > MAX_DESC_WORDS:
        raise ValidationError(f"Description is {n} words. The limit is {MAX_DESC_WORDS} words.")
    if len(v) > 1500:
        raise ValidationError("Description is too long. Keep it under 1500 characters.")


def options(text):
    """"Black, White" -> ["Black", "White"] (comma or new line separated, no duplicates)."""
    seen, out = set(), []
    for part in re.split(r"[,\n]", text or ""):
        v = part.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


class User(AbstractUser):  # staff = admin, everyone else = reseller. username = phone
    phone = models.CharField(max_length=11, unique=True, null=True)
    status = models.CharField(max_length=10, default="pending")  # pending / approved / blocked
    phone_verified = models.BooleanField(default=False)
    shop_name = models.CharField(max_length=100, blank=True)  # shop or Facebook page name
    address = models.CharField(max_length=300, blank=True)


class Category(models.Model):  # the 12 fixed top-level categories (created by a migration, the admin only adds sub categories)
    name = models.CharField(max_length=60, unique=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "shop_maincategory"
        ordering = ["position", "id"]

    def __str__(self):
        return self.name


class SubCategory(models.Model):  # category > sub category > product. The admin creates these.
    name = models.CharField(max_length=60)
    position = models.PositiveIntegerField(default=0)  # priority: smaller number shows first (the admin panel moves it with the arrows)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="subcategories", null=True)  # null only for rows that existed before categories were introduced

    class Meta:
        db_table = "shop_category"  # the old categories table: its rows became sub categories
        ordering = ["position", "name"]
        verbose_name_plural = "sub categories"
        constraints = [models.UniqueConstraint(fields=["category", "name"], name="unique_subcategory_per_category")]

    def __str__(self):
        return f"{self.category.name if self.category_id else '?'} > {self.name}"


class Product(models.Model):
    name = models.CharField(max_length=120)
    subcategory = models.ForeignKey(SubCategory, on_delete=models.PROTECT)
    base_price = models.PositiveIntegerField()  # supplier (admin) price
    in_stock = models.BooleanField(default=True)  # only "in stock / out of stock", no counting
    position = models.PositiveIntegerField(default=0)  # priority inside its sub category: smaller number shows first
    active = models.BooleanField(default=True)
    description = models.TextField(validators=[desc_ok])  # required, max 100 words
    size = models.CharField(max_length=100, blank=True)  # optional, comma separated: each one becomes an option the reseller must choose from, e.g. "M, L, XL"
    color = models.CharField(max_length=100, blank=True)  # optional, comma separated, e.g. "Black, White"
    video_url = models.URLField(blank=True, validators=[yt_ok])  # direct YouTube link

    class Meta:
        ordering = ["position", "-id"]

    @property
    def category(self):
        return self.subcategory.category

    @property
    def size_options(self):
        return options(self.size)

    @property
    def color_options(self):
        return options(self.color)


class Order(models.Model):
    reseller = models.ForeignKey(User, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=11)
    address = models.CharField(max_length=490)
    note = models.CharField(max_length=300, blank=True)  # optional note the reseller writes when placing the order (e.g. "call before delivery")
    cod = models.PositiveIntegerField()
    zone = models.CharField(max_length=14, default="outside_dhaka")  # inside_dhaka / sub_dhaka / outside_dhaka
    delivery_fee = models.PositiveIntegerField(default=0)  # the charge taken from this order's profit, saved so later price changes never touch it
    profit = models.IntegerField()  # calculated on the server only
    status = models.CharField(max_length=12, default="Pending")  # Pending Confirmed Shipped Delivered Cancelled Returned NeedsReview
    created = models.DateTimeField(auto_now_add=True)

    @property
    def code(self):
        return f"ED{1000 + self.id}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)  # a deleted product leaves the order intact
    name = models.CharField(max_length=120, default="")  # snapshot, so the order still reads well after the product is deleted or renamed
    category = models.CharField(max_length=60, blank=True)
    subcategory = models.CharField(max_length=60, blank=True)
    size = models.CharField(max_length=100, blank=True)  # the option the reseller chose (blank when the product has none)
    color = models.CharField(max_length=100, blank=True)
    qty = models.PositiveIntegerField()
    base_price = models.PositiveIntegerField()
    sell_price = models.PositiveIntegerField()


class Ledger(models.Model):  # balance = sum(amount). Never edit a balance directly.
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    amount = models.IntegerField()
    kind = models.CharField(max_length=12)  # profit / withdraw / refund / return_fee (a returned order's delivery charge, can push the balance below zero)
    ref = models.CharField(max_length=20, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:  # database-level guards: an order's profit is credited once, and its return fee is charged once
        constraints = [models.UniqueConstraint(fields=["kind", "ref"], condition=Q(kind="profit"), name="one_profit_per_order"),
                       models.UniqueConstraint(fields=["kind", "ref"], condition=Q(kind="return_fee"), name="one_return_fee_per_order")]


class Withdraw(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    account = models.CharField(max_length=60)
    status = models.CharField(max_length=10, default="Pending")  # Pending / Paid / Rejected
    created = models.DateTimeField(auto_now_add=True)


class Setting(models.Model):  # admin-editable numbers: delivery_inside, delivery_sub, delivery_outside, min_withdraw, hold_days
    key = models.CharField(max_length=30, unique=True)
    value = models.IntegerField()


class Otp(models.Model):
    phone = models.CharField(max_length=11)
    purpose = models.CharField(max_length=10)  # register / reset
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)


class ProductImage(models.Model):  # photos live in the database so they survive Render redeploys
    product = models.ForeignKey(Product, related_name="images", on_delete=models.CASCADE)
    data = models.BinaryField(editable=False)
    ctype = models.CharField(max_length=30, default="image/jpeg")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    class Meta:
        unique_together = [("user", "product")]


class CartItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    size = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=100, blank=True)
    qty = models.PositiveIntegerField()
    sell_price = models.PositiveIntegerField()

    class Meta:  # the same product in another size or color is a separate cart line
        constraints = [models.UniqueConstraint(fields=["user", "product", "size", "color"], name="one_cart_line_per_variant")]


class OrderNote(models.Model):  # admin notes shown on the reseller's order tracking
    order = models.ForeignKey(Order, related_name="notes", on_delete=models.CASCADE)
    status = models.CharField(max_length=12, blank=True)
    text = models.CharField(max_length=300)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class PayoutAccount(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    kind = models.CharField(max_length=8)  # bkash / nagad / bank
    number = models.CharField(max_length=40)
    holder = models.CharField(max_length=80, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    branch = models.CharField(max_length=80, blank=True)

    @property
    def label(self):
        return f"Bank: {self.bank_name} {self.number}" if self.kind == "bank" else f"{self.kind.title()} {self.number}"


class Notice(models.Model):  # announcements shown to resellers in the app's Notice tab
    title = models.CharField(max_length=120)
    body = models.TextField(max_length=2000, blank=True)
    pinned = models.BooleanField(default=False)  # pinned notices stay on top
    active = models.BooleanField(default=True)  # inactive = hidden from the app
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pinned", "-id"]
