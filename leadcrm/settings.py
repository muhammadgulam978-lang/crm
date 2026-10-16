import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# INSTALLED APPS / MIDDLEWARE / TEMPLATES
# ============================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'leads',
    'chatbot',
    'capi',
    'salesai',
    'marketing',
    
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'leads.middleware.RoleAccessMiddleware',
]

ROOT_URLCONF = 'leadcrm.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'leads.context_processors.sidebar_counts',
            ],
        },
    },
]

WSGI_APPLICATION = 'leadcrm.wsgi.application'


# ============================================================
# ENV LOAD
# ============================================================
load_dotenv(BASE_DIR / ".env")


# ============================================================
# CORE DJANGO
# ============================================================
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY environment variable is missing")

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        "localhost,127.0.0.1,.vercel.app"
    ).split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        "https://synergyintegratedsolutions-crm.vercel.app,https://*.vercel.app"
    ).split(",")
    if origin.strip()
]


# ============================================================
# DATABASE
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is missing")

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=0,
        ssl_require=not DEBUG,
    )
}


# ============================================================
# EMAIL — Synergy (default)
# ============================================================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "mail.synergyintegratedsolutions.pk")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "True").lower() == "true"
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "False").lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "bdo@synergyintegratedsolutions.pk")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
EMAIL_TIMEOUT = 30


# ============================================================
# EMAIL — Bilal Enterprise
# ============================================================
BILAL_EMAIL_HOST = os.getenv("BILAL_EMAIL_HOST", "mail.bilalenterprise.com")
BILAL_EMAIL_PORT = int(os.getenv("BILAL_EMAIL_PORT", "465"))
BILAL_EMAIL_USE_SSL = os.getenv("BILAL_EMAIL_USE_SSL", "True").lower() == "true"
BILAL_EMAIL_HOST_USER = os.getenv("BILAL_EMAIL_HOST_USER", "bdo@bilalenterprise.com")
BILAL_EMAIL_HOST_PASSWORD = os.getenv("BILAL_EMAIL_HOST_PASSWORD", "")
BILAL_IMAP_HOST = os.getenv("BILAL_IMAP_HOST", "mail.bilalenterprise.com")
BILAL_IMAP_PORT = int(os.getenv("BILAL_IMAP_PORT", "993"))


# ============================================================
# MULTI-ACCOUNT EMAIL
# ============================================================
EMAIL_ACCOUNTS = {
    "default": {
        "HOST": EMAIL_HOST,
        "PORT": EMAIL_PORT,
        "HOST_USER": EMAIL_HOST_USER,
        "HOST_PASSWORD": EMAIL_HOST_PASSWORD,
        "USE_SSL": EMAIL_USE_SSL,
        "USE_TLS": EMAIL_USE_TLS,
        "IMAP_HOST": os.getenv("IMAP_HOST", "mail.synergyintegratedsolutions.pk"),
        "IMAP_PORT": int(os.getenv("IMAP_PORT", "993")),
    },
    "bilal_enterprise": {
        "HOST": BILAL_EMAIL_HOST,
        "PORT": BILAL_EMAIL_PORT,
        "HOST_USER": BILAL_EMAIL_HOST_USER,
        "HOST_PASSWORD": BILAL_EMAIL_HOST_PASSWORD,
        "USE_SSL": BILAL_EMAIL_USE_SSL,
        "USE_TLS": False,
        "IMAP_HOST": BILAL_IMAP_HOST,
        "IMAP_PORT": BILAL_IMAP_PORT,
    },
}


# ============================================================
# META / FACEBOOK — App-level
# ============================================================
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0")


# ============================================================
# META — WhatsApp Cloud API
# ============================================================
META_WHATSAPP_TOKEN = os.getenv("META_WHATSAPP_TOKEN", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")


# ============================================================
# META — WhatsApp Calling
# ============================================================
META_WA_PHONE_NUMBER_ID = os.getenv("META_WA_PHONE_NUMBER_ID", "")
META_WA_ACCESS_TOKEN = os.getenv("META_WA_ACCESS_TOKEN", "")
META_WA_API_VERSION = os.getenv("META_WA_API_VERSION", "v20.0")
META_WA_VERIFY_TOKEN = os.getenv("META_WA_VERIFY_TOKEN", "")


# ============================================================
# META — Conversions API (CAPI)
# ============================================================
CAPI_META_ACCESS_TOKEN = os.getenv("CAPI_META_ACCESS_TOKEN", "")
CAPI_META_DATASET_ID = os.getenv("CAPI_META_DATASET_ID", "")
CAPI_META_API_VERSION = os.getenv("CAPI_META_API_VERSION", "v25.0")


# ============================================================
# META — Marketing API (ads management, optional)
# ============================================================
META_MARKETING_ACCESS_TOKEN = os.getenv("META_MARKETING_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "")
META_BUSINESS_ID = os.getenv("META_BUSINESS_ID", "")
META_PAGE_ID = os.getenv("META_PAGE_ID", "")
META_MARKETING_MONTHLY_BUDGET_LIMIT = float(
    os.getenv("META_MARKETING_MONTHLY_BUDGET_LIMIT", "0") or "0"
)


# ============================================================
# LINKEDIN — Lead Sync + Conversions API
# ============================================================
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "")

# Aliases — code uses CAPI_LINKEDIN_* names, env uses LINKEDIN_* names
CAPI_LINKEDIN_CLIENT_ID = LINKEDIN_CLIENT_ID
CAPI_LINKEDIN_CLIENT_SECRET = LINKEDIN_CLIENT_SECRET
CAPI_LINKEDIN_REDIRECT_URI = LINKEDIN_REDIRECT_URI

CAPI_LINKEDIN_ACCESS_TOKEN = os.getenv("CAPI_LINKEDIN_ACCESS_TOKEN", "")
CAPI_LINKEDIN_API_VERSION = os.getenv("CAPI_LINKEDIN_API_VERSION", "202606")
CAPI_LINKEDIN_SEND_USER_INFO = (
    os.getenv("CAPI_LINKEDIN_SEND_USER_INFO", "False").lower() == "true"
)


# ============================================================
# CAPI — shared
# ============================================================
CAPI_DEFAULT_CURRENCY = os.getenv("CAPI_DEFAULT_CURRENCY", "USD")
CAPI_DEFAULT_PHONE_COUNTRY_CODE = os.getenv("CAPI_DEFAULT_PHONE_COUNTRY_CODE", "92")
CAPI_EXTERNAL_ID_PREFIX = "sis-crm-lead-"
CAPI_ENCRYPTION_KEY = os.getenv("CAPI_ENCRYPTION_KEY", "")
CAPI_INTAKE_SECRET = os.getenv("CAPI_INTAKE_SECRET", "")


# ============================================================
# GOOGLE — OAuth / Calendar
# ============================================================
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    f"{SITE_BASE_URL}/google/callback/"
)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]


# ============================================================
# TWILIO — Calling
# ============================================================
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
AGENT_FALLBACK_NUMBER = os.getenv("AGENT_FALLBACK_NUMBER", "")


# ============================================================
# AI — Groq + OpenAI
# ============================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


# ============================================================
# AI Sales Assistant
# ============================================================
SALESAI_BACKGROUND_ANALYSIS = (
    os.getenv("SALESAI_BACKGROUND_ANALYSIS", "False").lower() == "true"
)


# ============================================================
# PRODUCTION / SECURITY
# ============================================================
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False").lower() == "true"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============================================================
# STATIC / MEDIA
# ============================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'