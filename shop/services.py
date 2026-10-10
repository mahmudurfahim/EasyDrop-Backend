from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from shipping import sms
from .models import Ledger, Order, Product, Setting, Withdraw

FINAL = {"Delivered", "Cancelled", "Returned"}
OPEN = ["Pending", "Confirmed", "Shipped", "NeedsReview"]


DEFAULTS = {"delivery_inside": settings.DELIVERY_INSIDE, "delivery_sub": settings.DELIVERY_SUB, "delivery_outside": settings.DELIVERY_OUTSIDE,
            "min_withdraw": settings.MIN_WITHDRAW, "hold_days": settings.HOLD_DAYS}
ZONES = {"inside_dhaka": "delivery_inside", "sub_dhaka": "delivery_sub", "outside_dhaka": "delivery_outside"}


def cfg(key):
    s = Setting.objects.filter(key=key).first()
    return s.value if s else DEFAULTS[key]


def delivery_fee(zone):
    if zone not in ZONES:
        raise ValueError("Choose the delivery area: inside_dhaka, sub_dhaka or outside_dhaka")
    return cfg(ZONES[zone])


def fees():  # all three charges, for the app to show
    return {z: cfg(k) for z, k in ZONES.items()}


def held(user):  # delivered profit still inside the hold period: shown as pending, not withdrawable
    cutoff = timezone.now() - timedelta(days=cfg("hold_days"))
    return Ledger.objects.filter(user=user, kind="profit", created__gt=cutoff).aggregate(s=Sum("amount"))["s"] or 0


def available(user):
    return (Ledger.objects.filter(user=user).aggregate(s=Sum("amount"))["s"] or 0) - held(user)


def pending(user):
    open_profit = Order.objects.filter(reseller=user, status__in=OPEN, profit__gt=0).aggregate(s=Sum("profit"))["s"] or 0
    return open_profit + held(user)


def notify(phone, text):
    try:
        sms.send_sms(phone, text)
    except Exception:
        pass  # an SMS problem must never break the main action


@transaction.atomic
def set_status(order, new):
    """Only place where an order reaches a final state. Credits profit once; a return charges the delivery fee."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in FINAL:
        raise ValueError(f"Order is already {order.status}")
    if new == "Delivered" and order.profit > 0:
        Ledger.objects.create(user=order.reseller, amount=order.profit, kind="profit", ref=order.code)
    if new == "Returned" and order.delivery_fee > 0:  # the courier trip was paid for nothing: charge it to the reseller, even below zero
        Ledger.objects.create(user=order.reseller, amount=-order.delivery_fee, kind="return_fee", ref=order.code)
    order.status = new
    order.save()
    return order


def confirm_order(o):
    """Pending -> Confirmed (no courier API: the parcel is booked by hand)."""
    if o.status != "Pending":
        raise ValueError("Only pending orders can be confirmed")
    o.status = "Confirmed"
    o.save()
    return o


def withdraw_resolve(pk, action):
    with transaction.atomic():
        w = Withdraw.objects.select_for_update().filter(pk=pk).first()
        if not w or w.status != "Pending":
            raise ValueError("Request not found or already handled")
        w.status = "Paid" if action == "paid" else "Rejected"
        if action != "paid":
            Ledger.objects.create(user=w.user, amount=w.amount, kind="refund", ref=str(w.id))
        w.save()
    return w


def manual_status(o, new):
    if new not in ("Shipped", "Delivered", "Returned"):
        raise ValueError("Choose Shipped, Delivered or Returned")
    if o.status not in ("Confirmed", "Shipped", "NeedsReview"):
        raise ValueError(f"A {o.status} order cannot be moved to {new}")
    if new == "Shipped":
        o.status = "Shipped"
        o.save()
        return o
    return set_status(o, new)  # Delivered credits the profit once, Returned charges the delivery fee
