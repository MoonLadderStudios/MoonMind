#!/bin/sh
# MM-1214: keep the deployment tool bundle visible when login shells rebuild PATH.
case ":${PATH:-}:" in
  *:/opt/moonmind-tools/bin:*) ;;
  *)
    if [ -n "${PATH:-}" ]; then
      export PATH="/opt/moonmind-tools/bin:${PATH}"
    else
      export PATH="/opt/moonmind-tools/bin"
    fi
    ;;
esac
