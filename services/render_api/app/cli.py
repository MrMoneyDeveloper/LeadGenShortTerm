import argparse
import json

from app import jobs


def main():
    parser = argparse.ArgumentParser(description="Explicit bounded job execution; never called during build")
    parser.add_argument("command", choices=["tick", "process-next", "adopt-destination", "adopt-layout", "recover", "confirm-empty-destination"])
    parser.add_argument("--next-position", type=int, help="adopt-destination: existing data rows to skip")
    parser.add_argument("--layout", choices=["sequential", "alphabetical"], help="adopt-layout: target layout")
    parser.add_argument("--confirm-spreadsheet-id", help="adopt-layout: must equal the bound/configured Sheet ID")
    parser.add_argument("--deliveries", nargs="+", help="recover: Drive lead-delivery-*.json files or folders")
    parser.add_argument("--tab-rows", help="recover: JSON file {tab: data_rows} from the SHARD_INDEX tab")
    parser.add_argument("--apply", action="store_true", help="recover: write (default: dry run)")
    args = parser.parse_args()
    if args.command == "recover":
        from pathlib import Path

        from app.services import recovery

        tab_rows = json.loads(Path(args.tab_rows).read_text(encoding="utf-8")) if args.tab_rows else {}
        print(json.dumps(recovery.recover(args.deliveries or [], tab_rows, apply=args.apply)))
        return
    if args.command == "confirm-empty-destination":
        from app.services import delivery

        print(json.dumps(delivery.confirm_empty_destination(args.confirm_spreadsheet_id or "")))
        return
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
