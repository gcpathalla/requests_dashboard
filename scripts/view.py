import sys
import time

def progress(p):
    print(f"PROGRESS: {p}", flush=True)

def main():
    if len(sys.argv) < 2:
        print("ERROR: No view name provided", flush=True)
        return 1

    view = sys.argv[1]

    print(f"Refreshing view: {view}", flush=True)
    progress(10)
    time.sleep(1)

    print("Validating view definition...", flush=True)
    progress(30)
    time.sleep(1)

    print(f"Executing refresh for {view}...", flush=True)
    progress(60)
    time.sleep(2)

    print("Optimizing view performance...", flush=True)
    progress(85)
    time.sleep(2)

    print("Finalizing refresh...", flush=True)
    progress(100)
    time.sleep(1)

    print("View refresh completed.", flush=True)
    time.sleep(1)

    return 0

if __name__ == "__main__":
    sys.exit(main())
