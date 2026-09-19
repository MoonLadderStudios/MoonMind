set -eu

policy_dir=${2:-${MOONMIND_EGRESS_POLICY_DIRECTORY:-/app/docker/sandbox-egress-proxy}}
live_dir=${3:-/run/moonmind-egress}
bundled_dir=$(dirname "$0")
provider_file="$policy_dir/omnigent-provider-domains.txt"
if [ ! -e "$provider_file" ]; then
    provider_file=/dev/null
fi
# Declared-dependency installs are on by default. Only an explicit false
# disables the class; an unset or malformed value keeps the supported default
# rather than silently closing the sandbox.
registry_file="$bundled_dir/package-registry-domains.txt"
case "$(printf '%s' "${MOONMIND_PACKAGE_REGISTRY_EGRESS_ENABLED:-true}" | tr 'A-Z' 'a-z')" in
    false|0|no|off|disabled) registry_file=/dev/null ;;
esac

validate() {
    LC_ALL=C awk '
        length($0) == 0 { next }
        {
            if (length($0) > 253 || $0 !~ /^[a-z0-9.-]+$/) exit 1
            n = split($0, labels, ".")
            if (n < 2 || labels[n] !~ /^[a-z]+$/ || length(labels[n]) < 2) exit 1
            if (labels[n] ~ /^(local|internal|localhost|test|invalid|onion|arpa)$/) exit 1
            for (i = 1; i <= n; i++) {
                if (length(labels[i]) < 1 || length(labels[i]) > 63) exit 1
                if (labels[i] !~ /^[a-z0-9]/ || labels[i] !~ /[a-z0-9]$/) exit 1
            }
        }
    ' "$1/omnigent-provider-domains.txt" "$1/package-registry-domains.txt"
}

case "$1" in
    start|prepare)
        mkdir -p "$live_dir"
        cp "$bundled_dir/squid.conf" "$live_dir/squid.conf"
        cp "$provider_file" "$live_dir/omnigent-provider-domains.txt"
        cp "$registry_file" "$live_dir/package-registry-domains.txt"
        validate "$live_dir"
        chmod 0444 "$live_dir/squid.conf" "$live_dir/omnigent-provider-domains.txt" \
            "$live_dir/package-registry-domains.txt"
        if [ "$1" = start ]; then
            ln -sf "$bundled_dir/squid.conf" /etc/squid/squid.conf
            ln -sf "$provider_file" /etc/squid/omnigent-provider-domains.txt
            ln -sf "$registry_file" /etc/squid/package-registry-domains.txt
            mkdir -p /var/log/squid /var/spool/squid
            chown proxy:proxy /var/log/squid /var/spool/squid
            exec squid -f "$live_dir/squid.conf" -NYC
        fi
        ;;
    check)
        validate "$live_dir"
        cmp "$bundled_dir/squid.conf" "$live_dir/squid.conf"
        cmp "$provider_file" "$live_dir/omnigent-provider-domains.txt"
        cmp "$registry_file" "$live_dir/package-registry-domains.txt"
        squid -k parse -f "$live_dir/squid.conf"
        ;;
    *) exit 1 ;;
esac
