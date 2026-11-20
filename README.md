# Hotel-AI-Claude

Alpha version of an AI-powered hotel reception system with voice and chat reservations in English and Polish.

## Features

- **Voice Calls**: Browser-based voice interface using WebRTC and MediaRecorder API
- **Text Chat**: Multi-turn conversational reservation flow
- **Bilingual Support**: English and Polish via language parameter (`?lang=en|pl`)
- **Room Management**: Single, Double, and Suite rooms with dynamic pricing
- **Booking Engine**: Availability checking, booking creation, cancellation, check-in/out, and no-show marking
- **Management Dashboard**: Visual room grid with real-time status tiles (available / confirmed / checked-in / no-show)
- **AI Agents**: Tool-loop agent pattern for structured JSON responses
- **Calendar Sync**: Best-effort Google Calendar integration (optional)
- **Guest Accounts**: Username/password login, Google SSO, and a guest portal to view and link bookings
- **Docker Deployment**: Single-container setup with gunicorn

## Tech Stack

- **Backend**: Python 3.15, Django 4.2.16, Django REST Framework
- **LLM**: Ollama (qwen2.5:3b) via OpenAI-compatible API
- **STT/TTS**: Whisper via Ollama / native model fallback
- **Testing**: pytest-django, pytest-cov
- **Deployment**: Docker + gunicorn

## Quick Start (Local Development)

### Prerequisites

1. Install [Ollama](https://ollama.ai) and pull models:
   ```bash
   ollama pull qwen2.5:3b
   ollama pull whisper
   ```

2. Activate virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   .venv/Scripts/Activate.ps1  # PowerShell
   pip install -r requirements.txt
   ```

3. Run migrations and seed data:
   ```bash
   python manage.py migrate
   python manage.py seed_providers
   python manage.py seed_data
   ```

4. Start the dev server:
   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```

5. Visit http://localhost:8000/ in your browser.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Homepage with language switcher |
| `/dashboard/?lang=en|pl&date=YYYY-MM-DD` | GET | Management dashboard with room grid |
| `/chat/?lang=en|pl` | GET | Chat UI |
| `/chat/turn/` | POST | Chat message turn |
| `/voice/?lang=en|pl` | GET | Voice call UI |
| `/api/voice/turn/` | POST | Voice audio processing |
| `/api/room-types/` | GET | List room types |
| `/api/rooms/` | GET | List rooms |
| `/api/bookings/create/` | POST | Create booking |
| `/api/bookings/` | GET | List bookings |
| `/api/bookings/<id>/` | GET | Booking detail |
| `/api/bookings/<id>/cancel/` | POST | Cancel booking |
| `/api/bookings/<id>/check-in/` | POST | Check in guest |
| `/api/bookings/<id>/check-out/` | POST | Check out guest |
| `/api/bookings/<id>/no-show/` | POST | Mark no-show |
| `/api/availability/stream/` | SSE | Real-time availability events |
| `/api/admin/` | GET | Admin booking list |
| `/api/feed/` | GET | iCal calendar export |
| `/login/` | GET/POST | Guest login (django-allauth) |
| `/signup/` | GET/POST | Guest registration |
| `/logout/` | GET/POST | Guest logout |
| `/google/login/` | GET | Google SSO entry point |
| `/accounts/profile/` | GET | Guest portal: bookings + account |
| `/accounts/profile/update/` | POST | Update phone and language |
| `/accounts/bookings/<id>/link/` | POST | Claim an existing booking |
| `/admin/` | GET | Django admin panel |

## Docker Deployment

```bash
docker build -t hotel-ai-alpha .
docker run -p 8000:8000 --add-host=host.docker.internal:host-gateway hotel-ai-alpha
```

After container startup, seed providers:
```bash
docker exec -it <container_id> python manage.py seed_providers
```

## Testing

```bash
python -m pytest hotel_ai/ -v
```

## Architecture

```
hotel_ai/
├── ai_providers/     # LLM provider abstraction, agent loop
│   ├── factory.py    # Provider selection by language
│   ├── agent.py      # ReservationAgent with tool loop
│   ├── openai.py     # OpenAI chat completion client
│   ├── anthropic.py  # Anthropic Claude client
│   ├── ollama.py     # Ollama OpenAI-compatible client
│   └── base.py       # Abstract base class
├── bookings/         # Room, booking, pricing models & API
├── calendar_integration/  # Google Calendar sync
├── chat/             # Text chat views and templates
├── voice/            # Voice call UI and processing
└── settings.py       # Django project settings
```

## Language Configuration

The agent uses different system prompts based on the `language` parameter:
- `en`: Professional American English receptionist
- `pl`: Natural, warm Polish receptionist

Both enforce strict JSON action format for programmatic responses while maintaining natural conversation flow.

## License

Internal prototype — not licensed.
