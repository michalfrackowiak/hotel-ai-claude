"""Start a local HTTPS dev server using a self-signed cert.

Browsers require HTTPS for getUserMedia (microphone/camera). This script
generates or reuses a localhost cert, then launches Django's WSGI app
behind an OpenSSL-wrapped HTTP server so you visit
https://127.0.0.1:8443/ in your browser.

Accept the certificate warning once; afterwards mic access works locally.
"""
from __future__ import annotations

import os
import ssl
import sys
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
SSL_DIR = os.path.join(HERE, "ssl")
CERT = os.path.join(SSL_DIR, "cert.pem")
KEY = os.path.join(SSL_DIR, "key.pem")
HTTPS_PORT = 8443


def ensure_cert():
    if not os.path.exists(SSL_DIR):
        os.makedirs(SSL_DIR)
    if not os.path.isfile(CERT) or not os.path.isfile(KEY):
        openssl = r"C:\Program Files\Git\usr\bin\openssl.exe"
        import subprocess

        subprocess.run(
            [
                openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", KEY,
                "-out", CERT,
                "-days", "365",
                "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
        )
    print(f"SSL cert ready at {CERT}")


class SecureWSGIServer(WSGIServer):
    """A WSGI server that wraps every accepted client connection in SSL."""

    request_queue_size = 128

    def __init__(self, server_address, handler_class, ssl_context, *args, **kwargs):
        super().__init__(server_address, handler_class, *args, **kwargs)
        self.ssl_context = ssl_context

    def get_request(self):
        """Accept a raw TCP connection and wrap it in SSL before handing off."""
        request, client_address = super().get_request()
        ssl_sock = self.ssl_context.wrap_socket(request, server_side=True)
        return ssl_sock, client_address

    def server_close(self):
        super().server_close()


if __name__ == "__main__":
    ensure_cert()

    sys.path.insert(0, HERE)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hotel_ai.settings")
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
    app = get_wsgi_application()

    server = SecureWSGIServer(("127.0.0.1", HTTPS_PORT), WSGIRequestHandler, ssl_context=ctx)
    server.set_app(app)

    print(f"\nDjango HTTPS dev server running at:")
    print(f"  https://127.0.0.1:{HTTPS_PORT}/")
    print(f"\nFirst load will show a certificate warning — accept it.")
    print("Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping HTTPS server.")
