import io
from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.utils.html import escape, format_html
from . import services, storage
from .images import BadImage, normalize
from .models import product_id_from_code, Category, Notice, Ledger, Order, OrderItem, OrderNote, Product, ProductImage, RetailCustomer, Setting, SubCategory, User, Withdraw
from .services import available, notify

admin.site.site_header = "EasyDrop Admin"
admin.site.register([Setting, SubCategory, Notice])


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):  # add / rename / delete here or in the admin panel (icons are uploaded in the admin panel)
    list_display = ("name", "position")
    fields = ("name", "position")


def prep(f):  # JPEG shrunk to at most 1000 px, never cropped (stored in Supabase Storage, or in the database when storage is not configured)
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
                data = prep(self.cleaned_data["upload"])
            except BadImage as e:
                raise forms.ValidationError(str(e))
            o.ctype = "image/jpeg"
            if storage.enabled():
                old = o.path
                try:
                    o.path = storage.upload(storage.PHOTO, storage.new_path("products", "jpg"), data, "image/jpeg")
                except storage.StorageError as e:
                    raise forms.ValidationError(f"{e}. Try again in a moment.")
                o.data = None
                if old:
                    storage.delete(storage.PHOTO, old)  # the photo this one replaces
            else:
                o.data, o.path = data, ""
        if commit:
            o.save()
        return o


class ImgInline(admin.TabularInline):
    model, form, extra = ProductImage, ImgForm, 3
    fields, readonly_fields = ("upload", "position", "preview"), ("preview",)

    def preview(self, o):
        return format_html('<img src="{}" height="70">', o.storage_url or f"/api/media/product/{o.pk}/") if o.pk else "-"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "subcategory", "base_price", "in_stock", "position", "active", "photos")
    list_filter, search_fields, inlines = ("subcategory__category", "subcategory", "active", "in_stock"), ("name",), [ImgInline]

    def get_search_results(self, request, queryset, search_term):  # the search box also understands a product code (EP1008 or 1008)
        qs, dup = super().get_search_results(request, queryset, search_term)
        pid = product_id_from_code(search_term)
        return (qs | queryset.filter(pk=pid), dup) if pid else (qs, dup)

    def photos(self, o):
        return o.images.count()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        p = form.instance
        if p.active and p.images.count() < 1:  # rule: at least 1 photo
            p.active = False
            p.save(update_fields=["active"])
            messages.warning(request, "A product needs at least 1 photo. It was saved as hidden.")


@admin.register(RetailCustomer)
class RetailCustomerAdmin(admin.ModelAdmin):  # customers that resellers typed into their orders (one row per order)
    list_display = ("name", "phone", "address", "reseller", "order_code", "status", "total_delivered", "total_returned", "delivered_cod", "returned_cod", "created")
    list_filter = ("status",)
    readonly_fields = ("status", "total_delivered", "total_returned", "delivered_cod", "returned_cod")  # kept up to date automatically
    search_fields = ("name", "phone", "address", "reseller__first_name", "reseller__phone")
    list_select_related = ("order", "reseller")

    def order_code(self, o):
        return o.order.code


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("first_name", "phone", "shop_name", "address", "status", "phone_verified", "balance")
    list_filter, search_fields = ("status", "phone_verified", "is_staff"), ("first_name", "phone", "shop_name")
    fields = ("first_name", "phone", "shop_name", "address", "status", "phone_verified", "is_active")
    actions = ["approve", "block", "delete_with_data"]

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

    @admin.action(description="DELETE selected resellers with ALL their orders, ledger and withdrawals (test accounts only)")
    def delete_with_data(self, request, qs):
        users = list(qs.filter(is_staff=False))
        if not users:
            self.message_user(request, "No reseller selected (admin accounts are skipped).", messages.WARNING)
            return None
        if request.POST.get("confirm") != "yes":
            ids = "".join(f'<input type="hidden" name="_selected_action" value="{u.pk}">' for u in users)
            names = "".join(f"<li>{escape(u.first_name)} {escape(u.phone)}: {Order.objects.filter(reseller=u).count()} orders, {Ledger.objects.filter(user=u).count()} ledger rows, {Withdraw.objects.filter(user=u).count()} withdrawals</li>" for u in users)
            return HttpResponse(f"<h2>Permanently delete these users and all their data?</h2><ul>{names}</ul><form method='post'>"
                                f"<input type='hidden' name='csrfmiddlewaretoken' value='{get_token(request)}'><input type='hidden' name='action' value='delete_with_data'>"
                                f"<input type='hidden' name='confirm' value='yes'>{ids}<button>Yes, delete everything</button> <a href='..'>Cancel</a></form>")
        with transaction.atomic():
            for u in users:
                Ledger.objects.filter(user=u).delete()
                Withdraw.objects.filter(user=u).delete()
                Order.objects.filter(reseller=u).delete()  # items, notes and retail customer rows cascade
                RetailCustomer.objects.filter(reseller=u).delete()
                u.delete()
        self.message_user(request, f"Deleted {len(users)} user(s) and their data.")


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
