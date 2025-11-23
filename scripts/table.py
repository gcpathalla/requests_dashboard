import sys
import time

def progress(p):
    print(f"PROGRESS: {p}", flush=True)

def main():
    if len(sys.argv) < 2:
        print("ERROR: No table name provided", flush=True)
        return 1

    table = sys.argv[1]
    print(f"Updating table: {table}", flush=True)

    progress(10)
    print("Checking table metadata...", flush=True)
    time.sleep(1)

    progress(25)
    print("Fetching latest data...", flush=True)
    time.sleep(2)

    progress(50)
    print("Applying updates...", flush=True)
    time.sleep(2)

    progress(75)
    print("Committing changes...", flush=True)
    time.sleep(1)

    progress(100)
    print("Table update completed.", flush=True)
    time.sleep(1)

    return 0

if __name__ == "__main__":
    sys.exit(main())
