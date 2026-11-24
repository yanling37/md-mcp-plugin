import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local MCP server for Marvelous Designer.")
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="Check the single-handle editing setup.")
    doctor.add_argument("--offline", action="store_true", help="Check local files without connecting to MD.")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        from .doctor import run
        report = run(offline=args.offline)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    from .server import create_server
    create_server().run(transport="stdio")


if __name__ == "__main__":
    raise SystemExit(main())
