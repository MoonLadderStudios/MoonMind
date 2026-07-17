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
