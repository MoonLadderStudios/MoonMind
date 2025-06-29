
import json
import re
import urllib.error
import urllib.request


def get_latest_version(package_name):
    """Fetches the latest version of a package from PyPI."""
    try:
        url = f"https://pypi.org/pypi/{package_name}/json"
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data["info"]["version"]
    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code} for {package_name}"
    except urllib.error.URLError as e:
        return f"URL Error for {package_name}: {e.reason}"
    except Exception as e:
        return f"Error fetching version for {package_name}: {e}"

def parse_dependencies_from_toml(file_path):
    """Manually parse dependencies from pyproject.toml file."""
    dependencies = []
    in_dependencies_section = False

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Check if we're entering the dependencies section
            if line == 'dependencies = [':
                in_dependencies_section = True
                continue

            # Check if we're leaving the dependencies section
            if in_dependencies_section and line == ']':
                break

            # Parse dependency lines
            if in_dependencies_section and line.startswith('"') and line.endswith('",'):
                # Remove quotes and comma, handle comments
                dep = line[1:-2]  # Remove quotes and comma
                if '#' in dep:
                    dep = dep.split('#')[0].strip()  # Remove comments
                dependencies.append(dep)

    return dependencies

def main():
    """
    Reads pyproject.toml, extracts dependencies, and finds the latest version for each.
    """
    try:
        dependencies = parse_dependencies_from_toml("pyproject.toml")

        print("Checking for latest versions of dependencies...")
        print("-" * 50)

        for dep in dependencies:
            # Use regex to handle version specifiers and extras
            match = re.match(r"([a-zA-Z0-9_-]+)(\[[a-zA-Z0-9_,]+\])?(.*)", dep)
            if match:
                package_name = match.group(1)
                current_spec = match.group(3).strip()
                latest_version = get_latest_version(package_name)
                print(f"{package_name:<40} | Current: {current_spec:<20} | Latest: {latest_version}")

    except FileNotFoundError:
        print("pyproject.toml not found in the current directory.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
