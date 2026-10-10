import re
from django.conf import settings
from django.db import transaction
from django.db.models import F, ProtectedError, Q, Sum
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser
from rest_framework.response import Response
from shipping.sms import SMSError
from . import otp
from .images import MAX_UPLOAD, BadImage, normalize
from .models import Category, Ledger, Notice, Order, OrderItem, Product, ProductImage, Setting, SubCategory, User, Withdraw
from .services import DEFAULTS, FINAL, available, cfg, confirm_order, fees, notify, pending, set_status, manual_status

PHONE = re.compile(r"01[3-9]\d{8}")
ORDER_STATES = ["Pending", "Confirmed", "Shipped", "Delivered", "Cancelled", "Returned", "NeedsReview"]


class Approved(BasePermission):
    def has_permission(self, r, v):
        return bool(r.user.is_authenticated and r.user.status == "approved" and r.user.phone_verified)


def err(msg, code=400):
    return Response({"error": msg}, status=code)


def oj(o):
    return {"id": o.id, "code": o.code, "reseller": o.reseller.first_name, "customer": o.customer_name, "phone": o.phone,
            "address": o.address, "note": o.note, "zone": o.zone, "delivery_fee": o.delivery_fee, "cod": o.cod, "profit": o.profit, "status": o.status,
            "created": o.created, "can_cancel": o.status == "Pending",
            "notes": [{"status": n.status, "text": n.text, "at": n.created} for n in o.notes.all()],
            "items": [{"product": i.name or (i.product.name if i.product else ""), "category": i.category, "subcategory": i.subcategory,
                       "size": i.size, "color": i.color, "qty": i.qty, "sell": i.sell_price, "base": i.base_price} for i in o.items.all()]}


class CategoryS(serializers.ModelSerializer):
    class Meta: model = Category; fields = ("id", "name", "position"); read_only_fields = ("name",)  # the 12 names are fixed, only the priority changes


class SubCategoryS(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), required=True, allow_null=False)  # pick one of the 12 categories
    category_name = serializers.CharField(source="category.name", read_only=True)
    class Meta: model = SubCategory; fields = ("id", "name", "category", "category_name", "position"); extra_kwargs = {"position": {"required": False}}

    def validate_name(self, v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Enter a name")
        return v

    def validate(self, d):
        cat = d.get("category", getattr(self.instance, "category", None))
        name = d.get("name", getattr(self.instance, "name", None))
        dup = SubCategory.objects.filter(category=cat, name__iexact=name)
        if self.instance:
            dup = dup.exclude(pk=self.instance.pk)
        if dup.exists():
            raise serializers.ValidationError({"name": "This sub category already exists in that category"})
        return d


class ProductS(serializers.ModelSerializer):
    subcategory_name = serializers.CharField(source="subcategory.name", read_only=True)
    category = serializers.IntegerField(source="subcategory.category_id", read_only=True)
    category_name = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    sizes = serializers.ListField(source="size_options", read_only=True)
    colors = serializers.ListField(source="color_options", read_only=True)
    class Meta:
        model = Product
        fields = ("id", "name", "subcategory", "subcategory_name", "category", "category_name", "base_price", "in_stock", "position", "active",
                  "description", "size", "color", "sizes", "colors", "video_url", "images", "photos")
        extra_kwargs = {"position": {"required": False}}

    def get_category_name(self, p):
        c = p.subcategory.category
        return c.name if c else ""

    def _url(self, i):
        rq = self.context.get("request")
        return rq.build_absolute_uri(f"/api/media/product/{i.id}/") if rq else f"/api/media/product/{i.id}/"

    def get_images(self, p):
        return [self._url(i) for i in p.images.all()]

    def get_photos(self, p):  # with ids, so the admin panel can delete one
        return [{"id": i.id, "url": self._url(i)} for i in p.images.all()]


class NoticeS(serializers.ModelSerializer):
    class Meta: model = Notice; fields = ("id", "title", "body", "pinned", "active", "created"); read_only_fields = ("created",)

    def validate_title(self, v):
        if not v.strip():
            raise serializers.ValidationError("Enter a title")
        return v.strip()


# ---------- auth / profile ----------
@api_view(["POST"])
@permission_classes([AllowAny])
def verify(r):
    phone, code = str(r.data.get("phone", "")), str(r.data.get("code", ""))
    u = User.objects.filter(phone=phone).first()
    if not u or not otp.check(phone, "register", code):
        return err("Wrong or expired code")
    u.phone_verified = True
    u.save()
    return Response({"message": "Phone verified. We will notify you after admin approval."})


@api_view(["POST"])
@permission_classes([AllowAny])
def send_code(r):  # purpose: register (resend) or reset (forgot password)
    phone, purpose = str(r.data.get("phone", "")), r.data.get("purpose")
    if purpose not in ("register", "reset"):
        return err("Invalid purpose")
    u = User.objects.filter(phone=phone).first()
    if u and (purpose == "reset" or not u.phone_verified):
        try:
            otp.issue(phone, purpose)
        except ValueError as e:
            return err(str(e), 429)
        except SMSError:
            return err("Could not send the SMS. Try again shortly.", 502)
    return Response({"message": "If this number is registered, a code has been sent."})


@api_view(["POST"])
@permission_classes([AllowAny])
def reset(r):
    phone, code, pw = str(r.data.get("phone", "")), str(r.data.get("code", "")), str(r.data.get("password", ""))
    u = User.objects.filter(phone=phone).first()
    if len(pw) < 6:
        return err("Password must be 6+ characters")
    if not u or not otp.check(phone, "reset", code):
        return err("Wrong or expired code")
    u.set_password(pw)
    u.phone_verified = True
    u.save()
    return Response({"message": "Password changed. You can log in now."})


@api_view(["GET"])
def me(r):
    u = r.user
    return Response({"name": u.first_name, "phone": u.phone, "shop_name": u.shop_name, "address": u.address, "status": u.status, "phone_verified": u.phone_verified, "is_admin": u.is_staff,
                     "available": available(u), "pending": pending(u)})


# ---------- reseller ----------
@api_view(["GET"])
def categories(r):  # 12 fixed categories, each with its sub categories
    out = [{"id": c.id, "name": c.name, "subcategories": []} for c in Category.objects.all()]
    by_id = {c["id"]: c for c in out}
    for s in SubCategory.objects.filter(category__isnull=False).order_by("position", "name"):
        by_id[s.category_id]["subcategories"].append({"id": s.id, "name": s.name})
    return Response(out)


@api_view(["GET"])
@permission_classes([Approved])
def notices(r):
    return Response(list(Notice.objects.filter(active=True).values("id", "title", "body", "pinned", "created")))


@api_view(["GET"])
@permission_classes([Approved])
def wallet(r):
    u = r.user
    return Response({"available": available(u), "pending": pending(u), "min_withdraw": cfg("min_withdraw"),
                     "delivery_charges": fees(),
                     "ledger": list(Ledger.objects.filter(user=u).order_by("-id")[:50].values("amount", "kind", "ref", "created")),
                     "withdraws": list(Withdraw.objects.filter(user=u).order_by("-id")[:50].values("id", "amount", "account", "status", "created"))})


# ---------- admin ----------
class Safe(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]

    def destroy(self, *a, **k):
        try:
            return super().destroy(*a, **k)
        except ProtectedError:
            return err("It is already used by products or orders. Deactivate it instead.", 409)


def _move(items, obj, direction):
    """Priority: swap `obj` with its neighbour in `items` (already in display order) and number the whole list 1..n."""
    items = list(items)
    ids = [x.pk for x in items]
    if obj.pk not in ids:
        return
    i = ids.index(obj.pk)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
    for n, it in enumerate(items, start=1):
        if it.position != n:
            it.position = n
            it.save(update_fields=["position"])


class Movable:  # adds POST .../<id>/move/ {"direction": "up" | "down"} to a viewset
    def scope(self, obj):
        raise NotImplementedError

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        d = request.data.get("direction")
        if d not in ("up", "down"):
            return err('direction must be "up" or "down"')
        obj = self.get_object()
        _move(self.scope(obj), obj, d)
        return Response({"ok": True})


class AdminProducts(Movable, Safe):  # deleting is always allowed: orders already placed keep their own copy of the product's details
    queryset = (Product.objects.select_related("subcategory__category").prefetch_related("images")
                .order_by("subcategory__category__position", "subcategory__position", "subcategory__name", "position", "-id"))
    serializer_class = ProductS

    def scope(self, obj):  # a product moves inside its own sub category
        return Product.objects.filter(subcategory_id=obj.subcategory_id).order_by("position", "-id")


class AdminCategories(Movable, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """The 12 categories are fixed: they can be listed, and only their priority (position) can be changed."""
    permission_classes = [IsAdminUser]
    http_method_names = ["get", "patch", "post", "head", "options"]  # post is only for .../move/
    queryset = Category.objects.all()
    serializer_class = CategoryS
    pagination_class = None

    def scope(self, obj):
        return Category.objects.all()


class AdminSubCategories(Movable, Safe):
    queryset = SubCategory.objects.select_related("category").order_by("category__position", "position", "name")
    serializer_class = SubCategoryS

    def scope(self, obj):  # a sub category moves inside its own category
        return SubCategory.objects.filter(category_id=obj.category_id).order_by("position", "name")


class AdminNotices(Safe):
    queryset = Notice.objects.all()
    serializer_class = NoticeS


@api_view(["POST"])
@permission_classes([IsAdminUser])
def product_images(r, pk):  # multipart: one or more files in "images" (or "image"). Photos keep their shape, big ones are shrunk to 1000 px.
    p = Product.objects.filter(pk=pk).first()
    if not p:
        return err("Product not found", 404)
    files = r.FILES.getlist("images") + r.FILES.getlist("image")
    if not files:
        return err("Choose at least one photo (form field name: images)")
    if len(files) > 8:
        return err("Upload up to 8 photos at a time")
    if p.images.count() + len(files) > 10:
        return err("A product can have up to 10 photos")
    ready = []
    for f in files:
        if f.size > MAX_UPLOAD:
            return err(f"{f.name}: the photo is bigger than 8 MB")
        try:
            ready.append(normalize(f))
        except BadImage as e:
            return err(f"{f.name}: {e}")
    pos = (p.images.order_by("-position").values_list("position", flat=True).first() or 0) + 1
    for k, data in enumerate(ready):
        ProductImage.objects.create(product=p, data=data, ctype="image/jpeg", position=pos + k)
    p = Product.objects.select_related("subcategory__category").prefetch_related("images").get(pk=pk)
    return Response(ProductS(p, context={"request": r}).data, status=201)


@api_view(["DELETE"])
@permission_classes([IsAdminUser])
def product_image_delete(r, pk, image_id):
    n, _ = ProductImage.objects.filter(pk=image_id, product_id=pk).delete()
    if not n:
        return err("Photo not found", 404)
    p = Product.objects.select_related("subcategory__category").prefetch_related("images").get(pk=pk)
    return Response(ProductS(p, context={"request": r}).data)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_orders(r):
    qs = Order.objects.select_related("reseller").prefetch_related("items", "notes").order_by("-id")
    if r.GET.get("status"):
        qs = qs.filter(status=r.GET["status"])
    if r.GET.get("zone"):
        qs = qs.filter(zone=r.GET["zone"])
    q = r.GET.get("q", "").strip()
    if q:  # order code (ED1008 or 1008), customer name/phone, reseller name/phone
        digits = re.sub(r"\D", "", q)
        cond = (Q(customer_name__icontains=q) | Q(phone__icontains=q) | Q(note__icontains=q) | Q(reseller__first_name__icontains=q) | Q(reseller__phone__icontains=q))
        if digits and q.upper().startswith("ED") or (digits and digits == q and int(digits[:9]) > 1000):
            cond |= Q(id=int(digits[:9]) - 1000)
        qs = qs.filter(cond)
    return Response([oj(o) for o in qs[:200]])


@api_view(["POST"])
@permission_classes([IsAdminUser])
def order_action(r, pk, action):
    o = Order.objects.select_related("reseller").filter(pk=pk).first()
    if not o:
        return err("Order not found", 404)
    try:
        if action == "confirm":
            if o.status != "Pending":
                return err("Only pending orders can be confirmed")
            o = confirm_order(o)
        elif action == "status":  # manual: {"status": "Shipped" | "Delivered" | "Returned"}
            o = manual_status(o, r.data.get("status"))
        elif action == "cancel":
            if o.status != "Pending":
                return err("Only pending orders can be cancelled. If the parcel is already confirmed, mark it Returned.")
            o = set_status(o, "Cancelled")
        elif action == "resolve":  # for NeedsReview: partial delivery, lost parcel, etc.
            st = r.data.get("status")
            if o.status != "NeedsReview" or st not in ("Delivered", "Returned"):
                return err("Resolve works on NeedsReview orders with status Delivered or Returned")
            if "profit" in r.data:
                o.profit = int(r.data["profit"])
                o.save()
            o = set_status(o, st)
        else:
            return err("Unknown action", 404)
    except ValueError as e:
        return err(str(e))
    return Response(oj(o))


@api_view(["GET"])
@permission_classes([IsAdminUser])
def reseller_list(r):
    return Response([{"id": u.id, "name": u.first_name, "phone": u.phone, "status": u.status, "verified": u.phone_verified, "shop_name": u.shop_name, "address": u.address, "balance": available(u)}
                     for u in User.objects.filter(is_staff=False).order_by("-id")])


@api_view(["POST"])
@permission_classes([IsAdminUser])
def reseller_status(r, pk):
    s, u = r.data.get("status"), User.objects.filter(pk=pk, is_staff=False).first()
    if not u or s not in ("pending", "approved", "blocked"):
        return err("Reseller not found or invalid status")
    u.status, u.is_active = s, s != "blocked"
    u.save()
    if s == "approved":
        notify(u.phone, "EasyDrop: your reseller account is approved. You can start ordering now.")
    return Response({"id": u.id, "status": u.status})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_withdraws(r):
    rows = list(Withdraw.objects.select_related("user").order_by("-id")[:200]
                .values("id", "amount", "account", "status", "created", reseller=F("user__first_name"), phone=F("user__phone"), uid=F("user_id")))
    bal = dict(Ledger.objects.filter(user_id__in={r["uid"] for r in rows}).order_by().values_list("user_id").annotate(s=Sum("amount")))
    for r_ in rows:  # the reseller's current balance, so the admin sees it next to the request
        r_["balance"] = bal.get(r_.pop("uid"), 0)
    return Response(rows)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def withdraw_action(r, pk, action):
    with transaction.atomic():
        w = Withdraw.objects.select_for_update().filter(pk=pk).first()
        if not w or w.status != "Pending":
            return err("Request not found or already handled")
        if action == "paid":
            w.status = "Paid"
        elif action == "reject":
            w.status = "Rejected"
            Ledger.objects.create(user=w.user, amount=w.amount, kind="refund", ref=str(w.id))
        else:
            return err("Unknown action", 404)
        w.save()
    return Response({"id": w.id, "status": w.status})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def dashboard(r):
    return Response({"orders": {s: Order.objects.filter(status=s).count() for s in ORDER_STATES},
                     "resellers_pending": User.objects.filter(is_staff=False, status="pending").count(),
                     "withdraws_pending": Withdraw.objects.filter(status="Pending").count(),
                     "reseller_balances": Ledger.objects.aggregate(s=Sum("amount"))["s"] or 0})


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def admin_settings(r):
    if r.method == "POST":
        for k, val in r.data.items():
            try:
                val = int(val)
            except (TypeError, ValueError):
                return err(f"{k} must be a whole number")
            if k not in DEFAULTS or val < 0:
                return err(f"Unknown setting or negative value: {k}")
            Setting.objects.update_or_create(key=k, defaults={"value": val})
    return Response({k: cfg(k) for k in DEFAULTS})
