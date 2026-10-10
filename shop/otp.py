import hashlib, hmac, secrets
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from shipping import sms
from .models import Otp


def _h(phone, code):
    return hashlib.sha256(f"{settings.SECRET_KEY}:{phone}:{code}".encode()).hexdigest()


def issue(phone, purpose):
    last = Otp.objects.filter(phone=phone, purpose=purpose).order_by("-id").first()
    if last and timezone.now() - last.created < timedelta(seconds=60):
        raise ValueError("Wait a minute before asking for another code")
    code = f"{secrets.randbelow(10**6):06d}"
    Otp.objects.create(phone=phone, purpose=purpose, code_hash=_h(phone, code))
    sms.send_sms(phone, f"EasyDrop code: {code}. Valid for 5 minutes. Do not share it.")


def check(phone, purpose, code):
    o = Otp.objects.filter(phone=phone, purpose=purpose, used=False, created__gte=timezone.now() - timedelta(minutes=5)).order_by("-id").first()
    if not o or o.attempts >= 5:
        return False
    o.attempts += 1
    ok = hmac.compare_digest(o.code_hash, _h(phone, str(code)))
    o.used = ok
    o.save()
    return ok
