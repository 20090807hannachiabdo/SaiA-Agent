import os
import sys
import threading
from core.web_server import run_web_server
from storage.database import Database
from ui.app_gui import start_app


def start_web_server():
    try:
        run_web_server()
    except Exception:
        pass


def create_database():
    db = Database(DEV=True)
    db.create_database()


def main():
    # 1. Initialize and ensure database schema
    create_database()

    # 2. Start Flask server in background thread
    web_server_thread = threading.Thread(target=start_web_server, daemon=True)
    web_server_thread.start()

    # 3. Launch desktop GUI application
    start_app()

    # 4. Clean exit
    os._exit(0)


if __name__ == "__main__":
    main()
