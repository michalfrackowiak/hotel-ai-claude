from django.urls import path

from .views import call_page, stream_voice_turn, voice_history, voice_turn

app_name = "voice"

urlpatterns = [
    path("turn/", voice_turn, name="voice-turn"),
    path("stream_turn/", stream_voice_turn, name="voice-stream-turn"),
    path("", call_page, name="call-page"),
    path("history/", voice_history, name="voice-history"),
]