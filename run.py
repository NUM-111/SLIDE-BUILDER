"""Start the loopback-only local web application."""

import argparse

from local_builder.server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local presentation builder")
    parser.add_argument("--port", type=int, default=8765, help="Local HTTP port (default: 8765)")
    args = parser.parse_args()
    serve(port=args.port)


if __name__ == "__main__":
    main()