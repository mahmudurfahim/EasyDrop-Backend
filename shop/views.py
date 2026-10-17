import re
from django.conf import settings
from django.db import transaction
from django.db.models import F, Max, ProtectedError, Q, Sum
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission, IsAdminUser
from rest_framework.response import Response
from shipping.sms import SMSError
from . import otp, storage
from .images import MAX_UPLOAD, BadImage, normalize, normalize_icon
from .models import Category, Ledger, Notice, Order, OrderItem, Product, ProductImage, Setting, SubCategory, User, Withdraw, unset_last
from .services import DEFAULTS, FINAL, available, cfg, confirm_order, fees, notify, pending, set_status, set_tracking, manual_status

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
            "created": o.created, "can_cancel": o.status == "Pending", "tracking_url": o.tracking_url,
            "notes": [{"status": n.status, "text": n.text, "at": n.created} for n in o.notes.all()],
            "items": [{"product": i.name or (i.product.name if i.product else ""), "category": i.category, "subcategory": i.subcategory,
                       "size": i.size, "color": i.color, "qty": i.qty, "sell": i.sell_price, "base": i.base_price} for i in o.items.all()]}


def icon_url(rq, s, kind="subcategory"):  # kind: "subcategory" or "category". None when there is no icon; ?v= changes on every replace so caches refresh
    if s.icon_path:  # stored in Supabase: every upload has a new file name, so the link itself is the cache key
        return storage.public_url(storage.ICON, s.icon_path)
    if not s.icon_data:
        return None
    path = f"/api/media/{kind}/{s.id}/?v={s.icon_ver}"
    return rq.build_absolute_uri(path) if rq else path


class CategoryS(serializers.ModelSerializer):
    icon = serializers.SerializerMethodField()
    class Meta: model = Category; fields = ("id", "name", "position", "icon"); extra_kwargs = {"position": {"min_value": 1, "required": False}, "name": {"validators": []}}

    def get_icon(self, c):
        return icon_url(self.context.get("request"), c, "category")

    def validate_name(self, v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Enter a name")
        dup = Category.objects.filter(name__iexact=v)
        if self.instance:
            dup = dup.exclude(pk=self.instance.pk)
        if dup.exists():
            raise serializers.ValidationError("This category already exists")
        return v


class SubCategoryS(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), required=True, allow_null=False)  # pick one of the 12 categories
    category_name = serializers.CharField(source="category.name", read_only=True)
    icon = serializers.SerializerMethodField()
    class Meta: model = SubCategory; fields = ("id", "name", "category", "category_name", "position", "icon"); extra_kwargs = {"position": {"required": False}}

    def get_icon(self, s):
        return icon_url(self.context.get("request"), s)

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
    code = serializers.CharField(read_only=True)  # automatic, e.g. EP1008
    sizes = serializers.ListField(source="size_options", read_only=True)
    colors = serializers.ListField(source="color_options", read_only=True)
    class Meta:
        model = Product
        fields = ("id", "code", "name", "subcategory", "subcategory_name", "category", "category_name", "base_price", "in_stock", "position", "active",
                  "description", "size", "color", "sizes", "colors", "video_url", "images", "photos")
        extra_kwargs = {"position": {"required": False}}

    def get_category_name(self, p):
        c = p.subcategory.category
        return c.name if c else ""

    def _url(self, i):
        if i.path:
            return i.storage_url
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
    out = [{"id": c.id, "name": c.name, "icon": icon_url(r, c, "category"), "subcategories": []} for c in Category.objects.all()]
    by_id = {c["id"]: c for c in out}
    for s in SubCategory.objects.filter(category__isnull=False).order_by(unset_last(), "position", "name"):
        by_id[s.category_id]["subcategories"].append({"id": s.id, "name": s.name, "icon": icon_url(r, s)})
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


def place(obj, scope):
    """Priority by number. `obj` keeps the number it was given; whatever already sat on that number moves down by one,
    and so on down the line until there is a free number. Example: 1,2,3 exist and a new item is set to 1 -> it becomes 1, the others 2,3,4."""
    n = obj.position
    if n < 1:  # 0 = no priority: shown after the numbered ones
        return
    want = n
    for o in scope.exclude(pk=obj.pk).filter(position__gte=n).order_by("position", "id"):
        if o.position > want:  # a free number: nothing further needs to move
            break
        want += 1
        o.position = want
        o.save(update_fields=["position"])


class Placed:  # after saving, put the item on its priority number
    def scope(self, obj):
        raise NotImplementedError

    def perform_create(self, serializer):
        obj = serializer.save()
        if "position" in self.request.data:
            place(obj, self.scope(obj))

    def perform_update(self, serializer):
        obj = serializer.save()
        if "position" in self.request.data:
            place(obj, self.scope(obj))


class AdminProducts(Placed, Safe):  # deleting is always allowed: orders already placed keep their own copy of the product's details
    queryset = (Product.objects.select_related("subcategory__category").prefetch_related("images")
                .order_by(unset_last("subcategory__category__position"), "subcategory__category__position",
                          unset_last("subcategory__position"), "subcategory__position", "subcategory__name",
                          unset_last(), "position", "-id"))
    serializer_class = ProductS

    def scope(self, obj):  # a product's number counts inside its own sub category
        return Product.objects.filter(subcategory_id=obj.subcategory_id)


class AdminCategories(Placed, Safe):
    """Categories can be added, renamed, given a priority number and deleted (a category that still has sub categories cannot be deleted)."""
    queryset = Category.objects.all()
    serializer_class = CategoryS
    pagination_class = None

    def scope(self, obj):
        return Category.objects.all()

    def perform_create(self, serializer):
        if not serializer.validated_data.get("position"):  # no number given: the new category goes last
            serializer.save(position=(Category.objects.aggregate(m=Max("position"))["m"] or 0) + 1)
        else:
            super().perform_create(serializer)

    def destroy(self, request, *a, **k):
        c = self.get_object()
        if c.subcategories.exists():
            return err("This category still has sub categories. Delete or move them first.", 409)
        old = c.icon_path
        res = super().destroy(request, *a, **k)
        if old and res.status_code < 300:
            storage.delete(storage.ICON, old)  # the icon file goes with the category
        return res


class AdminSubCategories(Placed, Safe):
    queryset = SubCategory.objects.select_related("category").order_by("category__position", unset_last(), "position", "name")
    serializer_class = SubCategoryS

    def scope(self, obj):  # a sub category's number counts inside its own category
        return SubCategory.objects.filter(category_id=obj.category_id)


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
    paths = []
    if storage.enabled():  # upload everything first: if one fails, nothing is half saved
        try:
            for data in ready:
                paths.append(storage.upload(storage.PHOTO, storage.new_path("products", "jpg"), data, "image/jpeg"))
        except storage.StorageError as e:
            for x in paths:
                storage.delete(storage.PHOTO, x)
            return err(f"{e}. Try again in a moment.", 502)
    for k, data in enumerate(ready):
        if paths:
            ProductImage.objects.create(product=p, path=paths[k], ctype="image/jpeg", position=pos + k)
        else:  # storage not configured (local development): keep the bytes in the database as before
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


def _icon(r, s, out):  # shared by categories and sub categories. POST multipart field "icon": replaces the icon. DELETE: removes it.
    old = s.icon_path
    if r.method == "DELETE":
        s.icon_data, s.icon_path = None, ""
    else:
        f = r.FILES.get("icon")
        if not f:
            return err("Choose an icon image (form field name: icon)")
        if f.size > MAX_UPLOAD:
            return err("The icon is bigger than 8 MB")
        try:
            data = normalize_icon(f)
        except BadImage as e:
            return err(str(e))
        if storage.enabled():
            try:
                s.icon_path = storage.upload(storage.ICON, storage.new_path("icons", "png"), data, "image/png")
            except storage.StorageError as e:
                return err(f"{e}. Try again in a moment.", 502)
            s.icon_data = None
        else:  # storage not configured (local development): keep the bytes in the database as before
            s.icon_data, s.icon_path = data, ""
    s.icon_ver += 1
    s.save(update_fields=["icon_data", "icon_path", "icon_ver"])
    if old and old != s.icon_path:
        storage.delete(storage.ICON, old)  # the replaced / removed icon file
    return Response(out(s, context={"request": r}).data)


@api_view(["POST", "DELETE"])
@permission_classes([IsAdminUser])
def subcategory_icon(r, pk):
    s = SubCategory.objects.select_related("category").filter(pk=pk).first()
    return _icon(r, s, SubCategoryS) if s else err("Sub category not found", 404)


@api_view(["POST", "DELETE"])
@permission_classes([IsAdminUser])
def category_icon(r, pk):
    c = Category.objects.filter(pk=pk).first()
    return _icon(r, c, CategoryS) if c else err("Category not found", 404)


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
            o = manual_status(o, r.data.get("status"), r.data.get("tracking_url"))  # tracking_url is optional (Steadfast link)
        elif action == "tracking":  # set / change / clear the Steadfast link later: {"tracking_url": "https://steadfast.com.bd/tl/..."}
            if o.status not in ("Shipped", "NeedsReview", "Delivered", "Returned"):
                return err("The tracking link can be added once the order is shipped")
            o = set_tracking(o, r.data.get("tracking_url"))
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
                     "orders_total": Order.objects.count(),
                     "resellers_total": User.objects.filter(is_staff=False).count(),
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
