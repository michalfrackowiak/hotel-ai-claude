from django.urls import path

from .views import chat_page, chat_turn

app_name = "chat"

urlpatterns = [
    path("", chat_page, name="chat-page"),
    path("turn/", chat_turn, name="chat-turn"),
]