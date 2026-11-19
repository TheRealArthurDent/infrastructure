#!/bin/sh
# Managed by Ansible (resticprofile role). Metrics of the offsite copy (#60), run after each copy and its retention:
#   offsite-metrics.sh <source> <offsite repository> <start of the copy, epoch seconds>
# writes the duration, size and snapshot count of the source's offsite repository to offsite_<source>.prom and, where
# the target's shell has df, its disk usage to offsite_disk.prom. Telegraf reads both (monitoring_agent role).
set -eu
source=$1
repository=$2
started=$3
dir=${METRICS_DIR:-/metrics}

stats=$(restic -r "$repository" --password-file /run/secrets/restic_offsite_passphrase stats --mode raw-data --json)
value() { printf '%s' "$stats" | sed -n "s/.*\"$1\":\([0-9][0-9]*\).*/\1/p"; }
size=$(value total_size)
snapshots=$(value snapshots_count)
[ -n "$size" ] && [ -n "$snapshots" ] || { echo "unexpected restic stats output: $stats" >&2; exit 1; }
{
  echo "resticprofile_offsite_duration_seconds{source=\"$source\"} $(( $(date +%s) - started ))"
  echo "resticprofile_offsite_size_bytes{source=\"$source\"} $size"
  echo "resticprofile_offsite_snapshots{source=\"$source\"} $snapshots"
} > "$dir/offsite_$source.prom.tmp"
mv "$dir/offsite_$source.prom.tmp" "$dir/offsite_$source.prom"

# The whole target, snapshots of the provider included: what counts against its quota. df answers in 1K blocks.
if usage=$(ssh offsite df 2>/dev/null); then
  printf '%s\n' "$usage" | awk '$2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ { size = $2; used = $3 }
    END { if (size) printf "resticprofile_offsite_disk_size_bytes %.0f\nresticprofile_offsite_disk_used_bytes %.0f\n",
      size * 1024, used * 1024 }' > "$dir/offsite_disk.prom.tmp"
  if [ -s "$dir/offsite_disk.prom.tmp" ]; then
    mv "$dir/offsite_disk.prom.tmp" "$dir/offsite_disk.prom"
  else
    rm -f "$dir/offsite_disk.prom.tmp"
  fi
fi
