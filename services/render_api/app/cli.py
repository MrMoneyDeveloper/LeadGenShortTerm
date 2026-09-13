import argparse
import json

from app import jobs


def main():
    parser = argparse.ArgumentParser(description="Explicit bounded job execution; never called during build")
    parser.add_argument("command", choices=["tick", "process-next"])
    args = parser.parse_args()
    if args.command == "tick":
        jobs.schedule_tick()
    print(json.dumps(jobs.run_next()))


if __name__ == "__main__":
    main()
