import subprocess
import sys

def main():
    print("Stopping and removing Temporal-related services and volumes...")

    # Run docker compose down -v
    cmd = ["docker", "compose", "down", "-v", "--remove-orphans"]

    try:
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        print(result.stdout)
        print("Teardown completed successfully. Environment is now clean.")
    except subprocess.CalledProcessError as e:
        print(f"Error during teardown: {e.stderr}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
