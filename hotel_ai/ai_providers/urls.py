from rest_framework import generics

from django.urls import path

from .models import AIProvider, AgentSettings
from .serializers import AIProviderSerializer, AgentSettingsSerializer


class AIProviderListCreateView(generics.ListCreateAPIView):
    queryset = AIProvider.objects.all()
    serializer_class = AIProviderSerializer


class AIProviderDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = AIProvider.objects.all()
    serializer_class = AIProviderSerializer


class AgentSettingsListCreateView(generics.ListCreateAPIView):
    queryset = AgentSettings.objects.all()
    serializer_class = AgentSettingsSerializer


class AgentSettingsDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = AgentSettings.objects.all()
    serializer_class = AgentSettingsSerializer


app_name = "ai_providers"

urlpatterns = [
    path("providers/", AIProviderListCreateView.as_view(), name="provider-list"),
    path("providers/<int:pk>/", AIProviderDetailView.as_view(), name="provider-detail"),
    path(
        "agent-settings/",
        AgentSettingsListCreateView.as_view(),
        name="agent-settings-list",
    ),
    path(
        "agent-settings/<int:pk>/",
        AgentSettingsDetailView.as_view(),
        name="agent-settings-detail",
    ),
]