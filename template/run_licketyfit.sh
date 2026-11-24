#!/bin/bash
set -euo pipefail

production=$production
datatools=$datatools
checkout=$checkout
python=$python
image=$image
setup=$setup
input=$input
output=$output
events=$events
particle=$particle
energy=$energy
seeding=$seeding
cpus=$cpus
container=$container
userns=$userns
adapter=$adapter
fingerprint=$fingerprint
sources=$sources

test -r "$$input" || { printf 'Missing WCSim input: %s\n' "$$input" >&2; exit 1; }
test -f "$$datatools/root_utils/event_dump.py" || { printf 'DataTools unavailable: %s\n' "$$datatools" >&2; exit 1; }
test -f "$$checkout/scripts/run_wcsim.py" || { printf 'LicketyFit unavailable: %s\n' "$$checkout" >&2; exit 1; }
test -x "$$python" || { printf 'Python unavailable: %s\n' "$$python" >&2; exit 1; }
test ! -e "$$output" || { printf 'Output already exists: %s\n' "$$output" >&2; exit 1; }
"$$python" "$$production/licketyfit_production.py" --verify-source "$$checkout" "$$datatools" "$$python" "$$production" "$$sources"
input_hash=$$("$$python" "$$production/licketyfit_production.py" --hash "$$input")

scratch=$$(mktemp -d "$${TMPDIR:-/tmp}/licketyfit.XXXXXXXX")
trap 'rm -rf -- "$$scratch"' EXIT
input_name=$$(basename "$$input")
"$$container" exec $$userns -B "$$production:/mnt" -B "$$datatools:/datatools:ro" -B "$$scratch:/scratch" "$$image" \
    bash -c 'set -eo pipefail; source /opt/entrypoint.sh; source "$$1"; export PYTHONPATH="/datatools$${PYTHONPATH:+:$$PYTHONPATH}"; root -l -b -q "/mnt/template/check_wcsim.C(\"/mnt/out/$$2\",$$3)"; python3 /datatools/root_utils/event_dump.py "/mnt/out/$$2" -d /scratch' -- "$$setup" "$$input_name" "$$events"

npz="$$scratch/$${input_name%.root}.npz"
result="$$scratch/result.dict"
"$$python" "$$adapter" "$$checkout" "$$npz" "$$result" "$$events" "$$particle" "$$energy" "$$seeding" "$$cpus"
"$$python" "$$production/licketyfit_production.py" --verify-source "$$checkout" "$$datatools" "$$python" "$$production" "$$sources"
"$$python" "$$adapter" --publish "$$input" "$$result" "$$output" "$$fingerprint" "$$events" "$$input_hash" \
    "$$checkout" "$$datatools" "$$particle" "$$energy" "$$seeding" "$$cpus" "$$sources"