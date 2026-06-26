from django.apps import AppConfig


class AgendamentosConfig(AppConfig):
    name = 'agendamentos'

    def ready(self):
        import agendamentos.checks  # noqa: F401
        import agendamentos.signals  # noqa: F401
