case ":${PATH:-}:" in
  *:/opt/venv/bin:*) ;;
  *) export PATH="/opt/venv/bin${PATH:+:$PATH}" ;;
esac
case ":${PATH:-}:" in
  *:/opt/moonmind-tools/bin:*) ;;
  *) export PATH="/opt/moonmind-tools/bin${PATH:+:$PATH}" ;;
esac
