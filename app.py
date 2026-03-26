import os

from app_core import app
import routes  # noqa: F401


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"Server starting (Waitress). Open: http://{display_host}:{port}")
    try:
        serve(app, host=host, port=port)
    except OSError as exc:
        print(f"Server start failed on {host}:{port} -> {exc}")
        print("Try another port: set PORT=5001 && python app.py")
