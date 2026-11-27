# Hotel AI

Hotel AI is an alpha hotel-reception and operations application built with Django. Guests can interact with a reservation agent by text or browser voice, while staff manage rooms and reservations through an operations dashboard and REST API. The application supports English and Polish.

## Capabilities

- **Reservations and inventory:** Room types and rooms, availability checks, date-based pricing rules, rate plans, promo codes, cancellation policies, and booking lifecycle actions (confirm, modify, cancel, check in, check out, and no-show).
- **Front-desk tools:** Occupancy and room-grid dashboard, arrivals and departures, booking activity, group bookings, waitlist, and room administration.
- **Guest accounts:** Registration and login, optional Google sign-in, a guest profile and booking portal, booking linking, notifications, and loyalty balances.
- **AI receptionist:** A configurable tool-using agent for reservation conversations, backed by OpenAI, Anthropic, or Ollama providers. Provider and agent settings are stored in Django and can be configured per language.
- **Voice and chat:** Browser interfaces for voice and text conversations, with configurable speech providers and streamed voice responses.
- **Optional integrations:** Best-effort Google Calendar synchronization and OpenLine voice/webhook integration. Credentials and external services are configured separately.

## Technology

- Python 3.11 (the version used by the Docker image), Django 4.2, and Django REST Framework
- SQLite by default; the Docker Compose configuration also defines PostgreSQL and Redis services
- OpenAI, Anthropic, and Ollama provider clients
- django-allauth for guest accounts and Google sign-in
- pytest and pytest-django for tests
- Docker, Gunicorn, and WhiteNoise for containerized serving

## Local development

Use Python 3.11 or a compatible version supported by the dependencies. For the default local setup, Django uses SQLite when `DATABASE_URL` is unset.

1. Create and activate a virtual environment, then install dependencies:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

   On macOS or Linux, activate with `source .venv/bin/activate` and copy the sample environment with `cp .env.example .env`.

2. Configure an AI provider. The `seed_providers` command sets up Ollama with `qwen2.5:3b` for English and Polish agent settings. For this option, install and start Ollama, then pull the model:

   ```sh
   ollama pull qwen2.5:3b
   ```

   Alternatively, configure an active `AIProvider` and `AgentSettings` in Django for OpenAI or Anthropic, supplying credentials through environment variables. Do not commit real credentials to the repository.

3. Apply migrations and initialize the AI provider settings:

   ```sh
   python manage.py migrate
   python manage.py seed_providers
   ```

4. Optionally add sample rooms, pricing, rate plans, promo codes, and a demo booking, then start the server:

   ```sh
   python manage.py seed_data
   python manage.py runserver
   ```

   **Note:** `seed_data` is intended for a fresh development database. Running it again deletes and recreates room inventory and bookings.

5. Open [http://localhost:8000/](http://localhost:8000/). The staff dashboard is at `/dashboard/`; browser chat and voice are at `/chat/` and `/voice/`.

Set `DATABASE_URL` to use a database other than the local SQLite default. See `.env.example` and `hotel_ai/settings.py` for the supported environment configuration. Google Calendar, Google sign-in, and OpenLine features require their respective external credentials and setup.

## Docker Compose

The development Compose file defines web, PostgreSQL, and Redis services. Copy `.env.example` to `.env`, configure the environment for your setup, and run:

```sh
docker compose up --build
```

The web container applies migrations, collects static files, and seeds the AI provider configuration on startup. The application is exposed at [http://localhost:8888/](http://localhost:8888/). The sample `.env.example` does not set `DATABASE_URL`, so Django uses SQLite unless you configure `DATABASE_URL` and provide a PostgreSQL driver for the Compose database. To load sample hotel data into the running application, use `docker compose exec web python manage.py seed_data`; the reset warning above also applies. `docker-compose.prod.yml` contains a separate production-oriented configuration with Nginx and Certbot services.

## Tests

Run the test suite from the repository root:

```sh
python -m pytest
```

## Project layout

```text
hotel_ai/
├── accounts/              Guest profiles, account portal, and booking linking
├── ai_providers/          Provider configuration and reservation agent
├── bookings/              Inventory, pricing, reservations, and REST API
├── calendar_integration/  Google Calendar synchronization
├── chat/                  Text conversation interface
├── voice/                 Voice conversation and OpenLine handling
├── settings.py            Django settings
└── urls.py                Root routes and health endpoint
templates/                 Dashboard, guest, chat, and voice pages
django-openline/           OpenLine integration package
```

## Main routes

- `/` — landing page
- `/dashboard/` — staff dashboard
- `/chat/` and `/voice/` — guest conversation interfaces
- `/api/` — booking, inventory, pricing, and guest-related REST endpoints
- `/health/` — application health response
- `/admin/` — Django administration
- `/accounts/profile/` — guest account portal
- `/integrations/openline/` — OpenLine integration endpoint

## License

Internal prototype; no license has been assigned.
