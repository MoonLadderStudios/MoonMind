#!/usr/bin/env sh
set -eu

REAL_DOCKER="${MOONMIND_DOCKER_REAL:-/usr/libexec/moonmind/docker-real}"

first_arg="${1:-}"
case "$first_arg" in
    ""|"-h"|"--help"|"help")
        exec "$REAL_DOCKER" "$@"
        ;;
    "-v"|"--version")
        exec "$REAL_DOCKER" "$@"
        ;;
esac

if [ "$first_arg" = "system" ]; then
    second_arg="${2:-}"
    if [ "$second_arg" = "help" ]; then
        exec "$REAL_DOCKER" "$@"
    fi
fi

if [ -n "${MOONMIND_DOCKER_ACTIVATION_COMMAND:-}" ]; then
    sh -c "$MOONMIND_DOCKER_ACTIVATION_COMMAND"
fi

exec "$REAL_DOCKER" "$@"
