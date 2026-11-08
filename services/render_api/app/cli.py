import argparse
import json

from app import jobs


def main():
    parser = argparse.ArgumentParser(description="Explicit bounded job execution; never called during build")
    parser.add_argument("command", choices=["tick", "process-next", "adopt-destination"])
    parser.add_argument("--next-position", type=int, help="adopt-destination: existing data rows to skip")
    args = parser.parse_args()
    if args.command == "adopt-destination":
        from app.services import delivery

        print(json.dumps(delivery.adopt_destination(args.next_position)))
        return
    if args.command == "tick":
        jobs.schedule_tick()
    print(json.dumps(jobs.run_next()))


if __name__ == "__main__":
    main()
