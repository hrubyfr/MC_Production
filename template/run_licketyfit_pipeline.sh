#!/bin/bash
set -euo pipefail

production=$production
image=$image
setup=$setup
container=$container
userns=$userns
config=$config
events=$events
seed=$seed
run_wcsim=$run_wcsim
run_mdt=$run_mdt
run_fq=$run_fq
python=$python
stage_fingerprint=$stage_fingerprint
adopt_unverified=$adopt_unverified

${cern_condor}export APPTAINER_BINDPATH=/afs,/cvmfs,/cvmfs/grid.cern.ch/etc/grid-security:/etc/grid-security,/cvmfs/grid.cern.ch/etc/grid-security/vomses:/etc/vomses,/eos,/etc/pki/ca-trust,/etc/tnsnames.ora,/run/user,/var/run/user
cd "$$production"
log="$$production/log/run$${config}.log"
exec >> "$$log" 2>&1
printf 'Host: %s\nDate: %s\nJob: %s\n' "$$(hostname)" "$$(date -u)" "$$config"

run_container() {
    "$$container" exec $$userns -B "$$production:/mnt" "$$image" \
        bash -c 'source /opt/entrypoint.sh && source "$$1" && shift && "$$@"' -- "$$setup" "$$@"
}

check_output() {
    local name="$$1" tree="$$2" origin="$$3" input="$${4:-}"
    test -f "$$production/out/$$name" || { printf 'Missing input: %s\n' "$$name" >&2; return 1; }
    if [[ ! -e "$$production/out/$$name.production.json" && "$$origin" != generated && "$$adopt_unverified" != 1 ]]; then
        printf 'Unrecorded input requires --adopt-unverified: %s\n' "$$name" >&2
        return 1
    fi
    run_container root -l -b -q "/mnt/template/check_wcsim.C(\"/mnt/out/$$name\",$$events,\"$$tree\")"
    "$$python" "$$production/licketyfit_production.py" --record-stage \
        "$$production/out/$$name" "$$input" "$$events" "$$stage_fingerprint" "$$origin"
}

wcsim="wcsim$${config}.root"
mdt="mdt$${config}.root"
fq="fq$${config}.root"
wcsim_origin=adopted-unverified
mdt_origin=adopted-unverified
fq_origin=adopted-unverified
if [[ ! -e "$$production/out/$$wcsim" && "$$run_wcsim" == 1 ]]; then
    run_container WCSim "/mnt/mac/wcsim$${config}.mac" "/mnt/mac/tuning_parameters$${config}.mac"
    wcsim_origin=generated
fi
check_output "$$wcsim" wcsimT "$$wcsim_origin"

reconstruction_status=0
$wcsim_reconstruction

if [[ "$$run_mdt" == 1 ]]; then
    if [[ ! -e "$$production/out/$$mdt" ]]; then
        "$$container" exec $$userns -B "$$production:/mnt" "$$image" bash -c \
            'source /opt/entrypoint.sh && source "$$1" && "$$MDTROOT/app/application/appWCTESingleEvent" -i "$$2" -p "$$MDTROOT/parameter/MDTParamenter_WCTE.txt" -o "$$3" -s "$$4" -n -1' \
            -- "$$setup" "/mnt/out/$$wcsim" "/mnt/out/$$mdt" "$$seed"
            mdt_origin=generated
    fi
            check_output "$$mdt" wcsimT "$$mdt_origin" "$$production/out/$$wcsim"
fi

$mdt_reconstruction

if [[ "$$run_fq" == 1 ]]; then
    check_output "$$mdt" wcsimT "$$mdt_origin" "$$production/out/$$wcsim"
    if [[ ! -e "$$production/out/$$fq" ]]; then
        "$$container" exec $$userns -B "$$production:/mnt" "$$image" bash -c \
            'source /opt/entrypoint.sh && source "$$1" && "$$FITQUN_ROOT/runfiTQunWC" -p "$$FITQUN_ROOT/ParameterOverrideFiles/nuPRISMBeamTest_16cShort_mPMT.parameters.dat" -r "$$2" "$$3"' \
            -- "$$setup" "/mnt/out/$$fq" "/mnt/out/$$mdt"
            fq_origin=generated
    fi
            check_output "$$fq" fiTQun "$$fq_origin" "$$production/out/$$mdt"
fi

$fq_reconstruction
exit "$$reconstruction_status"