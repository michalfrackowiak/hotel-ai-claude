#!/usr/bin/env python
"""Run the full test suite:  python runtests.py   (requires Django)."""
import sys

import django
from django.conf import settings


def main():
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-only",
        USE_TZ=True,
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.admin",
            "django_openline",
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]},
        }],
        ROOT_URLCONF="tests.urls",
        STATIC_URL="/static/",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        OPENLINE={},
    )
    django.setup()
    from django.test.utils import get_runner

    failures = get_runner(settings)(verbosity=2).run_tests(["tests"])
    sys.exit(bool(failures))


if __name__ == "__main__":
    main()
