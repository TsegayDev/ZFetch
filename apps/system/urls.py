from django.urls import path
from .views import SystemStatusView, SystemVersionView, SystemDependenciesView

urlpatterns = [
    path('status', SystemStatusView.as_view(), name='system_status'),
    path('version', SystemVersionView.as_view(), name='system_version'),
    path('dependencies', SystemDependenciesView.as_view(), name='system_dependencies'),
]
