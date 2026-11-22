from django.urls import path

from . import views

app_name = "openline"

urlpatterns = [
    path("", views.webhook, name="webhook"),
]
