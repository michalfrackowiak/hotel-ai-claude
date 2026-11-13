from django.urls import path

from .views import chat_turn

app_name = "chat"

urlpatterns = [
    path("turn/", chat_turn, name="chat-turn"),
]