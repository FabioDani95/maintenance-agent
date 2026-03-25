from __future__ import annotations

from flask import Flask

from api_routes import api_bp
from frontend_routes import frontend_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(frontend_bp)
    app.register_blueprint(api_bp)
    return app
