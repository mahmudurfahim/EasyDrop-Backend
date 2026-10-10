import os

import dj_database_url
from pathlib import Path

from dotenv import load_dotenv


# =========================================================
# BASE
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# =========================================================
# SECURITY
# =========================================================

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "dev-only-change-me",
)

DEBUG = os.getenv("DEBUG", "1") == "1"

if not DEBUG and SECRET_KEY in (
    "dev-only-change-me",
    "change-me",
    "change-this-to-a-long-random-secret-key",
):
    raise RuntimeError(
        "Set a real SECRET_KEY in .env when DEBUG=0"
    )


ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
    if host.strip()
]


# =========================================================
# APPLICATIONS
# =========================================================

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "corsheaders",
    "rest_framework",

    # Local apps
    "shop",
]


# =========================================================
# MIDDLEWARE
# =========================================================

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",

    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =========================================================
# URL CONFIGURATION
# =========================================================

ROOT_URLCONF = "config.urls"


# =========================================================
# TEMPLATES
# =========================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",

        "DIRS": [],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# =========================================================
# WSGI
# =========================================================

WSGI_APPLICATION = "config.wsgi.application"


# =========================================================
# DATABASE
# =========================================================

DB_NAME = os.getenv(
    "DB_NAME",
    "",
).strip()


if os.getenv("DATABASE_URL"):

    DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600)}

elif DB_NAME:

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",

            "NAME": DB_NAME,

            "USER": os.getenv(
                "DB_USER", "easydrop",
            ),

            "PASSWORD": os.getenv(
                "DB_PASSWORD", "easydrop",
            ),

            "HOST": os.getenv(
                "DB_HOST",
                "localhost",
            ),

            "PORT": os.getenv(
                "DB_PORT",
                "5432",
            ),
        }
    }

else:

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",

            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# =========================================================
# PASSWORD VALIDATION
# =========================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# =========================================================
# INTERNATIONALIZATION
# =========================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Dhaka"

USE_I18N = True

USE_TZ = True


# =========================================================
# STATIC FILES
# =========================================================

STATIC_URL = "static/"


# =========================================================
# DEFAULT PRIMARY KEY
# =========================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# =========================================================
# CUSTOM USER MODEL
# =========================================================

AUTH_USER_MODEL = "shop.User"


# =========================================================
# DJANGO REST FRAMEWORK
# =========================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],

    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],

    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
    ],

    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
    },
}


# =========================================================
# CORS
# =========================================================

CORS_ALLOW_ALL_ORIGINS = False

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]


# =========================================================
# BUSINESS CONFIGURATION
# =========================================================

# Delivery charge by area (taka). Admin can change these in the panel (Settings).
DELIVERY_INSIDE = int(os.getenv("DELIVERY_INSIDE", "60"))     # inside Dhaka
DELIVERY_SUB = int(os.getenv("DELIVERY_SUB", "100"))          # sub Dhaka
DELIVERY_OUTSIDE = int(os.getenv("DELIVERY_OUTSIDE", "120"))  # outside Dhaka

MIN_WITHDRAW = int(
    os.getenv(
        "MIN_WITHDRAW",
        "500",
    )
)

HOLD_DAYS = int(
    os.getenv(
        "HOLD_DAYS",
        "0",
    )
)


# =========================================================
# SMS - BULKSMSBD
# =========================================================

SMS_MOCK = (
    os.getenv(
        "SMS_MOCK",
        "0",
    ) == "1"
)

SMS_API_KEY = os.getenv(
    "SMS_API_KEY",
    "",
)

SMS_SENDER_ID = os.getenv(
    "SMS_SENDER_ID",
    "",
)

SMS_URL = os.getenv(
    "SMS_URL",
    "http://bulksmsbd.net/api/smsapi",
)


# =========================================================
# RENDER / PRODUCTION
# =========================================================

STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])

CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_ORIGINS", "https://*.onrender.com").split(",") if o.strip()]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
