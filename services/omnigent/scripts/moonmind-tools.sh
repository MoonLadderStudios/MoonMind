case ":${PATH:-}:" in
  *:/opt/moonmind-tools/bin:*) ;;
  *) export PATH="/opt/moonmind-tools/bin${PATH:+:$PATH}" ;;
esac
