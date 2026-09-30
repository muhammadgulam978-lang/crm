from django.apps import AppConfig


class CapiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'capi'
    verbose_name = 'Conversion API Center'

    def ready(self):
        from . import signals  # noqa: F401  (connects CRM -> conversion triggers)
