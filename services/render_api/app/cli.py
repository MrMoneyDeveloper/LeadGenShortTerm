import argparse
import json

from app import jobs


def main():
    parser = argparse.ArgumentParser(description="Explicit bounded job execution; never called during build")
    parser.add_argument("command", choices=["tick", "process-next", "adopt-destination", "adopt-layout"])
    parser.add_argument("--next-position", type=int, help="adopt-destination: existing data rows to skip")
    parser.add_argument("--layout", choices=["sequential", "alphabetical"], help="adopt-layout: target layout")
    parser.add_argument("--confirm-spreadsheet-id", help="adopt-layout: must equal the bound/configured Sheet ID")
    args = parser.parse_args()
    if args.command in {"adopt-destination", "adopt-layout"}:
        from app.services import delivery

        if args.command == "adopt-destination":
            print(json.dumps(delivery.adopt_destination(args.next_position)))
        else:
            print(json.dumps(delivery.adopt_layout(args.layout, args.confirm_spreadsheet_id or "")))
        return
    if args.command == "tick":
        jobs.schedule_tick()
    print(json.dumps(jobs.run_next()))


if __name__ == "__main__":
    main()
