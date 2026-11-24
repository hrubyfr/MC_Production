import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable, NamedTuple, Optional


class StageDefinition(NamedTuple):
    name: str
    enabled_attribute: str
    prerequisite: Optional[str]
    output_pattern: str
    recorded_completion: bool = False
    worker_template: Optional[str] = None
    source_factory: Optional[Callable] = None
    fingerprint_factory: Optional[Callable] = None
    worker_values: Optional[Callable] = None
    python_attribute: Optional[str] = None
    memory_attribute: Optional[str] = None
    cpus_attribute: Optional[str] = None
    walltime_attribute: Optional[str] = None
    schedd_attribute: Optional[str] = None
    validation_method: Optional[str] = None
    preflight_method: Optional[str] = None
    tree_name: Optional[str] = None


def licketyfit_sources(config):
    return source_hash(config.licketyfitdir, config.datatools, config.licketyfit_python, config.curdir)


def licketyfit_worker_values(config):
    return {
        "checkout": os.path.abspath(config.licketyfitdir),
        "datatools": os.path.abspath(config.datatools),
        "python": os.path.abspath(config.licketyfit_python),
        "particle": config.lf_particle,
        "energy": config.lf_energy_mev,
        "seeding": config.lf_seeding,
        "cpus": config.lf_cpus,
        "adapter": os.path.join(os.path.abspath(config.curdir), "template", "licketyfit.py"),
    }


STAGES = (
    StageDefinition("wcsim", "runWCSim", None, "out/wcsim{config}{index:04d}.root", tree_name="wcsimT"),
    StageDefinition("mdt", "runMDT", "wcsim", "out/mdt{config}{index:04d}.root", tree_name="wcsimT"),
    StageDefinition("fq", "runFQ", "mdt", "out/fq{config}{index:04d}.root", tree_name="fiTQun"),
    StageDefinition("licketyfit", "licketyfitdir", "wcsim", "lf/licketyfit{config}{index:04d}_{fingerprint}.dict",
                    True, "template/run_licketyfit.sh", licketyfit_sources, lambda config, sources: fingerprint(config, sources),
                    licketyfit_worker_values, "licketyfit_python", "lf_memory_mb", "lf_cpus", "lf_walltime",
                    "lf_condor_schedd", "validate_licketyfit", "preflight_licketyfit"),
)


def enabled_reconstructions(config):
    return tuple(stage for stage in STAGES if stage.worker_template and getattr(config, stage.enabled_attribute, None))


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hash(checkout, datatools, python, production):
    digest = hashlib.sha256()
    roots = (
        ("fit", Path(checkout) / "LicketyFit"),
        ("scripts", Path(checkout) / "scripts"),
        ("tables", Path(checkout) / "tables"),
        ("geometry", Path(checkout) / "Geometry"),
        ("runtime", Path(checkout) / "runtime_deps"),
        ("analysis", Path(checkout) / "analysis_tools"),
        ("datatools", Path(datatools) / "root_utils"),
    )
    suffixes = {".py", ".cpp", ".h", ".json", ".txt", ".dat", ".geo", ".csv", ".npy", ".npz"}
    for label, root in roots:
        if not root.is_dir():
            continue
        for directory, dirs, files in os.walk(root):
            dirs[:] = sorted(name for name in dirs if not name.startswith(".") and name not in {"__pycache__", "plots", "outputs"} and ".parts." not in name)
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() not in suffixes or not path.is_file():
                    continue
                digest.update((label + "/" + path.relative_to(root).as_posix() + "\0").encode())
                digest.update(bytes.fromhex(file_hash(path)))
    for label, path in (
        ("python", python),
        ("adapter", Path(production) / "template" / "licketyfit.py"),
        ("worker", Path(production) / "template" / "run_licketyfit.sh"),
        ("pipeline", Path(production) / "template" / "run_licketyfit_pipeline.sh"),
        ("stage-dispatch", Path(production) / "template" / "run_reconstruction_stage.sh"),
        ("validator", Path(production) / "template" / "check_wcsim.C"),
        ("helper", Path(production) / "licketyfit_production.py"),
        ("requirements", Path(checkout) / "requirements.txt"),
    ):
        if label == "requirements" and not Path(path).is_file():
            continue
        digest.update((label + "\0").encode())
        digest.update(bytes.fromhex(file_hash(path)))
    return digest.hexdigest()


def fingerprint(config, sources):
    values = {
        "sources": sources,
        "particle": config.lf_particle,
        "energy_mev": config.lf_energy_mev,
        "seeding": config.lf_seeding,
        "events": config.nevs,
        "cpus": config.lf_cpus,
        "configuration": config.get_config_string(),
        "seed": config.rngseed,
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stage_fingerprint(config):
    keys = ("rngseed", "nevs", "ParticleName", "ParticleKE", "ParticleDirx", "ParticleDiry",
            "ParticleDirz", "ParticlePosx", "ParticlePosy", "ParticlePosz", "wallD", "useCDS", "useBeam",
            "useRealisticBeam", "EnergySigma", "BeamSigma", "useUniform", "ParticleKELow", "ParticleKEHigh",
            "useCosmics", "savePrimaryParticleTruth", "saveAllParticleTruth", "wcsimdir", "wcsim_build_dir",
            "TankRadius", "TankHalfz", "siffile")
    values = {key: getattr(config, key) for key in keys}
    values["templates"] = {
        name: file_hash(Path(config.curdir) / "template" / name)
        for name in ("WCTE.mac", "tuning_parameters.mac", "check_wcsim.C")
    }
    if config.siffile and os.path.isfile(config.siffile):
        image = os.stat(config.siffile)
        values["image_stat"] = [image.st_size, image.st_mtime_ns]
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def stage_record(output, input_path, events, fingerprint):
    return {"output_sha256": file_hash(output), "input_sha256": file_hash(input_path) if input_path else None,
            "events": int(events), "fingerprint": fingerprint}


def stage_completed(output, input_path, events, fingerprint):
    try:
        with open(str(output) + ".production.json") as stream:
            record = json.load(stream)
        origin = record.pop("origin", None)
        return origin in ("generated", "adopted-unverified") and record == stage_record(output, input_path, events, fingerprint)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def record_stage(output, input_path, events, fingerprint, origin):
    path = Path(str(output) + ".production.json")
    record = stage_record(output, input_path, events, fingerprint)
    if origin not in ("generated", "adopted-unverified"):
        raise ValueError("Stage origin must be generated or adopted-unverified")
    record["origin"] = origin
    if path.exists():
        if not stage_completed(output, input_path, events, fingerprint):
            raise RuntimeError("Stage validation record is stale; inspect and move it before retrying: %s" % path)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def completed(input_path, output_path, expected_fingerprint, events):
    marker = Path(str(output_path) + ".json")
    output = Path(output_path)
    if not output.exists() and not marker.exists():
        return False
    try:
        with marker.open() as stream:
            data = json.load(stream)
        return (
            data["fingerprint"] == expected_fingerprint
            and data["events"] == events
            and data["input_sha256"] == file_hash(input_path)
            and data["output_sha256"] == file_hash(output)
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def scheduler_terminal(backend, job_id, schedd=None):
    if backend == "cedar":
        queue = subprocess.run(["squeue", "-h", "-j", job_id, "-o", "%T"], capture_output=True, text=True)
        if queue.returncode != 0:
            return None
        if queue.stdout.strip():
            return False
        history = subprocess.run(["sacct", "-n", "-P", "-j", job_id, "--format=JobIDRaw,State"], capture_output=True, text=True)
        if history.returncode != 0:
            return None
        for line in history.stdout.splitlines():
            fields = line.split("|")
            if len(fields) >= 2 and fields[0] == job_id:
                state_fields = fields[1].split()
                if not state_fields:
                    return None
                state = state_fields[0].rstrip("+")
                if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "REVOKED"}:
                    return True
                return False
        return None
    if backend == "condor":
        if not schedd:
            return None
        queue = subprocess.run(["condor_q", "-name", schedd, job_id, "-af", "JobStatus"], capture_output=True, text=True)
        if queue.returncode != 0:
            return None
        if queue.stdout.strip():
            return False
        history = subprocess.run(["condor_history", "-name", schedd, job_id, "-match", "1", "-af", "JobStatus"], capture_output=True, text=True)
        if history.returncode != 0:
            return None
        status = history.stdout.strip()
        return True if status in {"3", "4"} else None
    return None


def write_state(path, state):
    descriptor, temporary = tempfile.mkstemp(prefix=".submission-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def submit_once(path, backend, command, dry_run=False, schedd=None, is_complete=None):
    path = Path(path)
    with open(str(path) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            with path.open() as stream:
                state = json.load(stream)
            if dry_run:
                print("Dry-run: would reconcile previous submission %s" % path)
                return False
            if state.get("phase") != "submitted" or not state.get("job_id"):
                raise RuntimeError("Submission outcome is ambiguous; inspect the scheduler and ledger before retrying: %s" % path)
            terminal = scheduler_terminal(state.get("backend"), state["job_id"], state.get("schedd"))
            if terminal is False:
                print("Skipping active job %s on %s" % (state["job_id"], state["backend"]))
                return False
            if terminal is None:
                raise RuntimeError("Cannot determine scheduler state for job %s; refusing to resubmit" % state["job_id"])
        if dry_run:
            print("Dry-run: would submit %s" % command[-1])
            return False
        if is_complete is not None and is_complete():
            print("Skipping job completed during scheduler reconciliation")
            return False
        write_state(path, {"phase": "submitting", "backend": backend, "schedd": schedd})
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except OSError as exc:
            raise RuntimeError("Submission outcome is ambiguous; inspect %s: %s" % (path, exc)) from exc
        pattern = r"Submitted batch job\s+(\d+)" if backend == "cedar" else r"submitted to cluster\s+(\d+)"
        match = re.search(pattern, result.stdout, re.IGNORECASE)
        if result.returncode != 0 or not match:
            raise RuntimeError("Submission outcome is ambiguous; inspect %s: %s %s" % (path, result.stdout, result.stderr))
        write_state(path, {"phase": "submitted", "backend": backend, "job_id": match.group(1), "schedd": schedd})
        print(result.stdout.strip())
        return True


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "--verify-source":
        if source_hash(*sys.argv[2:6]) != sys.argv[6]:
            raise SystemExit("LicketyFit source or runtime changed after job generation")
    elif len(sys.argv) == 6 and sys.argv[1] == "--check-output":
        if not completed(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])):
            raise SystemExit("LicketyFit output is incomplete or stale")
    elif len(sys.argv) == 3 and sys.argv[1] == "--hash":
        print(file_hash(sys.argv[2]))
    elif len(sys.argv) == 7 and sys.argv[1] == "--record-stage":
        record_stage(sys.argv[2], sys.argv[3] or None, sys.argv[4], sys.argv[5], sys.argv[6])
    else:
        raise SystemExit("Expected --verify-source or --check-output arguments")