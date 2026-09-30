from django.apps import AppConfig


class SalesaiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'salesai'
    verbose_name = 'AI Sales Assistant'

    def ready(self):
        from . import signals  # noqa: F401
