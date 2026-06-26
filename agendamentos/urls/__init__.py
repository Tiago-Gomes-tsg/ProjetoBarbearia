from .cliente_urls import urlpatterns as cliente_urlpatterns
from .dashboard_urls import urlpatterns as dashboard_urlpatterns

urlpatterns = cliente_urlpatterns + dashboard_urlpatterns
