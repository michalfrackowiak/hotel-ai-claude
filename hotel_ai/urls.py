"""Root URL configuration for the Hotel-AI-Claude project."""

from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path


def health(request):
    return JsonResponse({"status": "ok", "service": "hotel-ai-claude"})


def home(request):
    language = request.GET.get("lang", "en")
    return render(request, "index.html", {"language": language})


urlpatterns = [
    path("health/", health, name="health"),
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("api/", include("hotel_ai.bookings.urls")),
    path("api/ai/", include("hotel_ai.ai_providers.urls")),
    path("api/calendar/", include("hotel_ai.calendar_integration.urls")),
    path("voice/", include("hotel_ai.voice.urls")),
    path("chat/", include("hotel_ai.chat.urls")),
]