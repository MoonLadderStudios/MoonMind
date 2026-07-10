# Contributing to MoonMind

Thanks for your interest in improving MoonMind. Issues and pull requests are welcome.

AI-assisted contributions are welcome, but contributors are responsible for reviewing, understanding, and testing everything they submit.

For substantial changes, please open an issue first so we can agree on the approach before implementation begins.

Do not include secrets, credentials, private data, private configuration, or unredacted logs in issues or pull requests.

## Development and testing

Clone the repository and initialize its submodules:

```bash
git clone https://github.com/MoonLadderStudios/MoonMind.git
cd MoonMind
git submodule update --init --recursive
```

Start the local application:

```bash
docker compose up -d
```

Open [http://localhost:7000](http://localhost:7000) to use the dashboard.

Run the relevant automated checks before opening a pull request:

```bash
# Unit tests
./tools/test_unit_docker.sh

# Integration tests for cross-service or infrastructure changes
./tools/test_integration.sh
```

After the first unit-test run, use `./tools/test_unit_docker.sh --no-build` for faster repeat runs.

Behavior changes should include a focused test when practical. Tests requiring live third-party provider credentials are not required unless a maintainer asks for them.

## Pull requests

Branch from `main`, complete the pull request template, and include tests or documentation when relevant. Include screenshots or a short recording for dashboard changes.

## Getting help

Open a GitHub issue for bugs, feature requests, or questions about a proposed contribution.
