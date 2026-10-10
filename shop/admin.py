import io
from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from . import services
from .images import BadImage, normalize
from .models import Category, Notice, Order, OrderItem, OrderNote, Product, ProductImage, Setting, SubCategory, User, Withdraw
from .services import available, notify

admin.site.site_header = "EasyDrop Admin"
admin.site.register([Setting, SubCategory, Notice])


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):  # the 12 categories are fixed: only their priority can be changed
    list_display = ("name", "position")
    fields, readonly_fields = ("name", "position"), ("name",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def prep(f):  # JPEG kept inside the database, shrunk to at most 1000 px, never cropped
    return normalize(f)


class ImgForm(forms.ModelForm):
    upload = forms.ImageField(required=False, label="Photo")

    class Meta:
        model = ProductImage
        fields = ("upload", "position")

    def clean(self):
        d = super().clean()
        if not self.instance.pk and not d.get("upload"):
            raise forms.ValidationError("Choose a photo for this row")
        return d

    def save(self, commit=True):
        o = super().save(commit=False)
        if self.cleaned_data.get("upload"):
            try:
                o.data, o.ctype = prep(self.cleaned_data["upload"]), "image/jpeg"
            except BadImage as e:
                raise forms.ValidationError(str(e))
        if commit:
            o.save()
        return o


class ImgInline(admin.TabularInline):
    model, form, extra = ProductImage, ImgForm, 3
    fields, readonly_fields = ("upload", "position", "preview"), ("preview",)

    def preview(self, o):
        return format_html('<img src="/api/media/product/{}/" height="70">', o.pk) if o.pk else "-"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "subcategory", "base_price", "in_stock", "position", "active", "photos")
    list_filter, search_fields, inlines = ("subcategory__category", "subcategory", "active", "in_stock"), ("name",), [ImgInline]

    def photos(self, o):
        return o.images.count()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        p = form.instance
        if p.active and p.images.count() < 1:  # rule: at least 1 photo
            p.active = False
            p.save(update_fields=["active"])
            messages.warning(request, "A product needs at least 1 photo. It was saved as hidden.")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("first_name", "phone", "shop_name", "address", "status", "phone_verified", "balance")
    list_filter, search_fields = ("status", "phone_verified", "is_staff"), ("first_name", "phone", "shop_name")
    fields = ("first_name", "phone", "shop_name", "address", "status", "phone_verified", "is_active")
    actions = ["approve", "block"]

    def has_add_permission(self, request):
        return False

    def balance(self, o):
        return available(o)

    @admin.action(description="Approve selected resellers (sends SMS)")
    def approve(self, request, qs):
        for u in qs.filter(is_staff=False):
            u.status, u.is_active = "approved", True
            u.save()
            notify(u.phone, "EasyDrop: your reseller account is approved. You can start ordering now.")

    @admin.action(description="Block selected resellers")
    def block(self, request, qs):
        qs.filter(is_staff=False).update(status="blocked", is_active=False)


class NoteInline(admin.TabularInline):
    model, extra, fields, readonly_fields = OrderNote, 1, ("status", "text"), ("status",)


class ItemInline(admin.TabularInline):
    model, extra, can_delete = OrderItem, 0, False
    fields = readonly_fields = ("name", "category", "subcategory", "size", "color", "qty", "base_price", "sell_price")

    def has_add_permission(self, *a, **k):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("code", "reseller", "customer_name", "phone", "zone", "cod", "delivery_fee", "profit", "status")
    list_filter, search_fields = ("status", "zone"), ("customer_name", "phone", "reseller__first_name", "reseller__phone")
    readonly_fields = ["code"] + [f.name for f in Order._meta.fields]
    inlines = [ItemInline, NoteInline]
    actions = ["confirm", "cancel_pending", "mark_shipped", "mark_delivered", "mark_returned"]

    def has_add_permission(self, request):
        return False

    def save_formset(self, request, form, formset, change):  # notes are stamped with the order's current status
        for o in formset.save(commit=False):
            if isinstance(o, OrderNote) and not o.pk:
                o.status = form.instance.status
            o.save()
        for o in formset.deleted_objects:
            o.delete()

    def _each(self, request, qs, fn, ok):
        for o in qs:
            try:
                fn(o)
                self.message_user(request, f"{o.code}: {ok}")
            except ValueError as e:
                self.message_user(request, f"{o.code}: {e}", messages.ERROR)

    @admin.action(description="Confirm (Pending)")
    def confirm(self, request, qs):
        self._each(request, qs, services.confirm_order, "confirmed")

    @admin.action(description="Cancel (Pending only)")
    def cancel_pending(self, request, qs):
        self._each(request, qs.filter(status="Pending"), lambda o: services.set_status(o, "Cancelled"), "cancelled")

    @admin.action(description="Mark Shipped")
    def mark_shipped(self, request, qs):
        self._each(request, qs, lambda o: services.manual_status(o, "Shipped"), "shipped")

    @admin.action(description="Mark Delivered (credits the reseller's profit)")
    def mark_delivered(self, request, qs):
        self._each(request, qs, lambda o: services.manual_status(o, "Delivered"), "delivered")

    @admin.action(description="Mark Returned (no profit, delivery charge taken from the balance)")
    def mark_returned(self, request, qs):
        self._each(request, qs, lambda o: services.manual_status(o, "Returned"), "returned")


@admin.register(Withdraw)
class WithdrawAdmin(admin.ModelAdmin):
    list_display, list_filter = ("id", "user", "amount", "account", "status", "created"), ("status",)
    readonly_fields = ("user", "amount", "account", "status")
    actions = ["paid", "reject"]

    def has_add_permission(self, request):
        return False

    def _do(self, request, qs, action):
        for w in qs:
            try:
                services.withdraw_resolve(w.pk, action)
            except ValueError as e:
                self.message_user(request, f"#{w.pk}: {e}", messages.ERROR)

    @admin.action(description="Mark as paid (after you sent the money)")
    def paid(self, request, qs):
        self._do(request, qs, "paid")

    @admin.action(description="Reject and refund balance")
    def reject(self, request, qs):
        self._do(request, qs, "reject")
