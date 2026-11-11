"""Run the OptiConverter API with ``python -m opticonverterbackend``."""

import os

from .app import app


def main():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
