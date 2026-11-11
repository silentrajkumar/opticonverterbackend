"""Compatibility entry point for the Flask application."""

from opticonverterbackend.__main__ import main
from opticonverterbackend.app import app

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
