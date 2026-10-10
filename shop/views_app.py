"""Reseller-app endpoints: signup, products with photos/video, favorites, cart, orders, accounts, withdraw, dashboard."""
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from shipping.sms import SMSError
from . import otp
from .models import CartItem, Favorite, Order, OrderItem, OrderNote, PayoutAccount, Product, ProductImage, User, Ledger, Withdraw
from .services import ZONES, available, cfg, delivery_fee, fees, pending, set_status
from .views import ORDER_STATES, PHONE, Approved, err, oj


@api_view(["POST"])
@permission_classes([AllowAny])
def register(r):
    d = r.data
    g = lambda k: str(d.get(k, "")).strip()
    name, phone, shop, addr, pw = g("name"), g("phone"), g("shop_name"), g("address"), str(d.get("password", ""))
    if not (name and shop and addr) or not PHONE.fullmatch(phone) or len(pw) < 6:
        return err("Name, 11-digit phone, shop/page name, address and a password of 6+ characters are required")
    if User.objects.filter(phone=phone).exists():
        return err("This phone number is already registered", 409)
    User.objects.create_user(username=phone, phone=phone, first_name=name, shop_name=shop[:100], address=addr[:300], password=pw)
    try:
        otp.issue(phone, "register")
    except (ValueError, SMSError) as e:
        print(f"[register SMS failed] {phone}: {e}")  # visible in Render logs; the person can ask for a new code
    return Response({"message": "Registered. Enter the SMS code, then wait for admin approval."}, status=201)


# ---------- products ----------
def pj(p, r, favs=()):
    sub = p.subcategory
    return {"id": p.id, "name": p.name, "category": sub.category_id, "category_name": sub.category.name if sub.category_id else "",
            "subcategory": sub.id, "subcategory_name": sub.name, "base_price": p.base_price,
            "in_stock": p.in_stock, "description": p.description, "size": p.size, "color": p.color,
            "sizes": p.size_options, "colors": p.color_options,  # the options a reseller must choose from (empty = nothing to choose)
            "video_url": p.video_url, "is_favorite": p.id in favs,
            "images": [r.build_absolute_uri(f"/api/media/product/{i.id}/") for i in p.images.all()]}


def _pq():  # only active products that have at least 1 photo are visible to resellers, in the admin's priority order
    return (Product.objects.filter(active=True).select_related("subcategory__category").prefetch_related("images")
            .annotate(n=Count("images")).filter(n__gte=1).order_by("subcategory__category__position", "subcategory__position", "position", "-id"))


def check_variant(p, size, color):
    """The reseller must pick one of the product's sizes and one of its colors (when the admin gave any). Returns the exact option names."""
    out = []
    for label, val, opts in (("size", size, p.size_options), ("color", color, p.color_options)):
        val = str(val or "").strip()
        if not opts:
            out.append("")  # nothing to choose for this product
            continue
        hit = next((o for o in opts if o.lower() == val.lower()), None)
        if not hit:
            raise ValueError(f"{p.name}: choose a {label} ({', '.join(opts)})")
        out.append(hit)
    return out[0], out[1]


def _favs(u):
    return set(Favorite.objects.filter(user=u).values_list("product_id", flat=True))


@api_view(["GET"])
@permission_classes([Approved])
def products(r):
    qs = _pq()
    if r.GET.get("category", "").isdigit():
        qs = qs.filter(subcategory__category_id=r.GET["category"])
    if r.GET.get("subcategory", "").isdigit():
        qs = qs.filter(subcategory_id=r.GET["subcategory"])
    if r.GET.get("q"):
        qs = qs.filter(name__icontains=r.GET["q"])
    f = _favs(r.user)
    return Response([pj(p, r, f) for p in qs])


@api_view(["GET"])
@permission_classes([Approved])
def product_detail(r, pk):
    p = _pq().filter(pk=pk).first()
    return Response(pj(p, r, _favs(r.user))) if p else err("Product not found", 404)


def media(request, pk):  # plain Django view: public, cached, not rate limited (app image loaders send no token)
    i = ProductImage.objects.filter(pk=pk).first()
    if not i:
        raise Http404
    resp = HttpResponse(bytes(i.data), content_type=i.ctype)
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


@api_view(["GET"])
@permission_classes([Approved])
def favorites(r):
    ids = _favs(r.user)
    return Response([pj(p, r, ids) for p in _pq().filter(id__in=ids)])


@api_view(["POST"])
@permission_classes([Approved])
def favorite_toggle(r, pk):
    if not Product.objects.filter(pk=pk, active=True).exists():
        return err("Product not found", 404)
    f, made = Favorite.objects.get_or_create(user=r.user, product_id=pk)
    if not made:
        f.delete()
    return Response({"favorite": made})


# ---------- orders ----------
def make_order(user, d, items):
    name, phone, addr = str(d.get("customer_name", "")).strip(), str(d.get("phone", "")), str(d.get("address", "")).strip()
    if not items or not name or not addr or not PHONE.fullmatch(phone):
        raise ValueError("Enter the customer's name, address, 11-digit phone and at least one item")
    note = str(d.get("note", "") or "").strip()[:300]  # optional
    zone = str(d.get("zone", ""))
    fee = delivery_fee(zone)  # raises a clear error when the area is missing or unknown
    try:
        with transaction.atomic():
            lines = []
            for it in items:
                p = Product.objects.select_related("subcategory__category").get(pk=it["product"], active=True)
                q, s = int(it["qty"]), int(it["sell_price"])
                if q < 1 or q > 100 or s < p.base_price:
                    raise ValueError(f"{p.name}: selling price must be at least ৳{p.base_price}" if s < p.base_price else f"{p.name}: quantity must be between 1 and 100")
                if not p.in_stock:
                    raise ValueError(f"{p.name} is out of stock")
                size, color = check_variant(p, it.get("size"), it.get("color"))
                lines.append((p, q, s, size, color))
            profit = sum((s - p.base_price) * q for p, q, s, _, _ in lines) - fee
            if profit <= 0:
                raise ValueError(f"Your extra profit must be more than the ৳{fee} delivery charge")
            o = Order.objects.create(reseller=user, customer_name=name, phone=phone, address=addr, note=note, profit=profit, zone=zone, delivery_fee=fee,
                                     cod=sum(s * q for _, q, s, _, _ in lines))
            for p, q, s, size, color in lines:
                sc = p.subcategory
                OrderItem.objects.create(order=o, product=p, name=p.name, category=sc.category.name if sc.category_id else "", subcategory=sc.name,
                                         size=size, color=color, qty=q, base_price=p.base_price, sell_price=s)
            OrderNote.objects.create(order=o, status="Pending", text="Order received")
    except (KeyError, TypeError, Product.DoesNotExist):
        raise ValueError("One of the items is invalid or no longer available")
    return o


@api_view(["GET", "POST"])
@permission_classes([Approved])
def orders(r):
    if r.method == "GET":
        qs = Order.objects.filter(reseller=r.user).prefetch_related("items", "notes").order_by("-id")
        if r.GET.get("status"):
            qs = qs.filter(status=r.GET["status"])
        return Response([oj(o) for o in qs])
    try:
        o = make_order(r.user, r.data, r.data.get("items") or [])
    except ValueError as e:
        return err(str(e))
    return Response(oj(o), status=201)


@api_view(["GET"])
@permission_classes([Approved])
def order_detail(r, pk):
    o = Order.objects.filter(pk=pk, reseller=r.user).first()
    return Response(oj(o)) if o else err("Order not found", 404)


@api_view(["POST"])
@permission_classes([Approved])
def order_cancel(r, pk):
    with transaction.atomic():
        o = Order.objects.select_for_update().filter(pk=pk, reseller=r.user).first()
        if not o:
            return err("Order not found", 404)
        if o.status != "Pending":
            return err(f"Only pending orders can be cancelled. This order is {o.status}.")
        o = set_status(o, "Cancelled")
        OrderNote.objects.create(order=o, status="Cancelled", text="Cancelled by reseller")
    return Response(oj(o))


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_note(r, pk):  # a note can be added or updated at any status
    o, text = Order.objects.filter(pk=pk).first(), str(r.data.get("text", "")).strip()[:300]
    if not o or not text:
        return err("Order not found or the note is empty", 404 if not o else 400)
    OrderNote.objects.create(order=o, status=o.status, text=text)
    return Response(oj(o))


# ---------- cart ----------
def _cart(r, zone=""):
    rows = CartItem.objects.filter(user=r.user).select_related("product__subcategory__category").prefetch_related("product__images").order_by("id")
    items = [{"id": c.id, "product": pj(c.product, r), "size": c.size, "color": c.color, "qty": c.qty, "sell_price": c.sell_price,
              "extra_profit": (c.sell_price - c.product.base_price) * c.qty} for c in rows]
    extra, fee = sum(i["extra_profit"] for i in items), (cfg(ZONES[zone]) if zone in ZONES else None)  # fee/profit stay empty until the area is chosen
    return {"items": items, "extra_profit": extra, "zone": zone if zone in ZONES else None, "delivery_charge": fee,
            "delivery_charges": fees(), "profit": (extra - fee if fee is not None else None) if items else 0,
            "cod_total": sum(c.sell_price * c.qty for c in rows)}


@api_view(["GET", "POST"])
@permission_classes([Approved])
def cart(r):
    if r.method == "POST":
        try:
            p = Product.objects.get(pk=r.data["product"], active=True)
            q, s = int(r.data["qty"]), int(r.data["sell_price"])
        except (KeyError, TypeError, ValueError, Product.DoesNotExist):
            return err("Send a valid product, qty and sell_price")
        if q < 1 or q > 100:
            return err("Quantity must be between 1 and 100")
        if not p.in_stock:
            return err(f"{p.name} is out of stock")
        if s < p.base_price:
            return err(f"Selling price must be at least ৳{p.base_price}")
        try:
            size, color = check_variant(p, r.data.get("size"), r.data.get("color"))
        except ValueError as e:
            return err(str(e))
        CartItem.objects.update_or_create(user=r.user, product=p, size=size, color=color, defaults={"qty": q, "sell_price": s})
    return Response(_cart(r, str(r.GET.get("zone", ""))))  # GET /cart/?zone=inside_dhaka shows the profit for that area


@api_view(["DELETE"])
@permission_classes([Approved])
def cart_remove(r, pk):
    CartItem.objects.filter(user=r.user, pk=pk).delete()
    return Response(_cart(r, str(r.GET.get("zone", ""))))


@api_view(["POST"])
@permission_classes([Approved])
def cart_checkout(r):
    items = [{"product": c.product_id, "qty": c.qty, "sell_price": c.sell_price, "size": c.size, "color": c.color} for c in CartItem.objects.filter(user=r.user)]
    try:
        o = make_order(r.user, r.data, items)
    except ValueError as e:
        return err(str(e))
    CartItem.objects.filter(user=r.user).delete()
    return Response(oj(o), status=201)


# ---------- payout accounts & withdraw ----------
def aj(a):
    return {"id": a.id, "kind": a.kind, "label": a.label, "number": a.number, "holder": a.holder, "bank_name": a.bank_name, "branch": a.branch}


@api_view(["GET", "POST"])
@permission_classes([Approved])
def accounts(r):
    if r.method == "POST":
        d = r.data
        kind, num, holder, bank = d.get("kind"), str(d.get("number", "")).strip(), str(d.get("holder", "")).strip(), str(d.get("bank_name", "")).strip()
        if kind in ("bkash", "nagad"):
            if not PHONE.fullmatch(num):
                return err("Enter an 11-digit mobile number")
        elif kind == "bank":
            if not (num and holder and bank):
                return err("Bank name, account holder and account number are required")
        else:
            return err("kind must be bkash, nagad or bank")
        if PayoutAccount.objects.filter(user=r.user).count() >= 5:
            return err("You can save up to 5 accounts")
        a = PayoutAccount.objects.create(user=r.user, kind=kind, number=num[:40], holder=holder[:80], bank_name=bank[:80], branch=str(d.get("branch", ""))[:80])
        return Response(aj(a), status=201)
    return Response([aj(a) for a in PayoutAccount.objects.filter(user=r.user).order_by("id")])


@api_view(["DELETE"])
@permission_classes([Approved])
def account_delete(r, pk):
    PayoutAccount.objects.filter(user=r.user, pk=pk).delete()
    return Response(status=204)


@api_view(["POST"])
@permission_classes([Approved])
def withdraw_create(r):
    try:
        amt = int(r.data.get("amount"))
    except (TypeError, ValueError):
        return err("Enter an amount")
    aid = str(r.data.get("account_id", ""))
    acc = PayoutAccount.objects.filter(user=r.user, pk=aid).first() if aid.isdigit() else None
    if amt < cfg("min_withdraw"):
        return err(f"Minimum withdraw is ৳{cfg('min_withdraw')}")
    if not acc:
        return err("Choose one of your saved accounts (account_id)")
    with transaction.atomic():
        User.objects.select_for_update().get(pk=r.user.pk)  # serialize concurrent requests
        if Withdraw.objects.filter(user=r.user, status="Pending").exists():
            return err("You already have a pending withdraw request. Wait until it is paid or rejected.", 409)
        if amt > available(r.user):
            return err("Amount is more than your available balance")
        w = Withdraw.objects.create(user=r.user, amount=amt, account=acc.label[:60])
        Ledger.objects.create(user=r.user, amount=-amt, kind="withdraw", ref=str(w.id))
    return Response({"id": w.id, "status": w.status}, status=201)


# ---------- reseller dashboard ----------
@api_view(["GET"])
@permission_classes([Approved])
def dashboard(r):
    u, qs = r.user, Order.objects.filter(reseller=r.user)
    cnt = {s: qs.filter(status=s).count() for s in ORDER_STATES}
    done = qs.filter(status="Delivered").aggregate(profit=Sum("profit"), sales=Sum("cod"))
    paid = Q(status="Delivered")
    rows = (qs.annotate(m=TruncMonth("created")).values("m")
            .annotate(orders=Count("id"), delivered=Count("id", filter=paid), profit=Sum("profit", filter=paid), sales=Sum("cod", filter=paid))
            .order_by("-m")[:12])
    return Response({"orders": cnt, "total_orders": qs.count(), "completed_orders": cnt["Delivered"],
                     "total_profit": done["profit"] or 0, "total_sales": done["sales"] or 0,
                     "available": available(u), "pending_profit": pending(u),
                     "withdrawn": Withdraw.objects.filter(user=u, status="Paid").aggregate(s=Sum("amount"))["s"] or 0,
                     "monthly": [{"month": x["m"].strftime("%Y-%m"), "orders": x["orders"], "delivered": x["delivered"],
                                  "profit": x["profit"] or 0, "sales": x["sales"] or 0} for x in rows]})
