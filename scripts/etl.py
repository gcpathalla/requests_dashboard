import time
import sys

def progress(p):
    print(f"PROGRESS: {p}", flush=True)

def main():
    if len(sys.argv) < 2:
        print("ERROR: No ETL item provided", flush=True)
        return 1

    item = sys.argv[1]

    print(f"Starting ETL for: {item}", flush=True)
    progress(5)
    time.sleep(1)

    print("Connecting to database...", flush=True)
    progress(15)
    time.sleep(1)

    print("Running transformation...", flush=True)
    progress(40)
    time.sleep(2)

    print("Loading data...", flush=True)
    progress(70)
    time.sleep(2)

    print("Finalizing ETL...", flush=True)
    progress(90)
    time.sleep(1)

    print("ETL completed!", flush=True)
    progress(100)
    time.sleep(1)

    return 0

if __name__ == "__main__":
    sys.exit(main())
