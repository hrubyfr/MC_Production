#!/usr/bin/env python3
import argparse
import importlib
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
import tempfile

import numpy as np


def load_launcher(checkout):
    sys.path.insert(0, str(Path(checkout).resolve()))
    launcher = importlib.import_module("scripts.run_wcsim")
    required = ("INPUT_FILE", "OUTPUT_FILE", "N_EVENTS", "EVENT_START_INDEX", "FIT_PARTICLE",
                "ENERGY_LABEL_MEV", "SEEDING_MODE", "NPROC", "MAX_INTERNAL_THREADS_PER_WORKER",
                "USE_TRUTH_ROOT", "USE_THROUGHGOING", "main")
    missing = [key for key in required if not hasattr(launcher, key)]
    if missing or not callable(getattr(launcher, "main", None)):
        raise RuntimeError("Incompatible LicketyFit launcher, missing or invalid: " + ", ".join(missing or ["main"]))
    return launcher


def preflight(checkout, particle, energy, seeding, events, cpus):
    if sys.version_info < (3, 10):
        raise RuntimeError("LicketyFit requires Python 3.10 or newer")
    launcher = load_launcher(checkout)
    for package in ("numpy", "numba", "scipy", "uproot", "awkward", "matplotlib", "pandas", "iminuit"):
        try:
            importlib.import_module(package)
        except (ImportError, OSError) as exc:
            raise RuntimeError("LicketyFit Python dependency %s is unavailable: %s" % (package, exc)) from exc
    geometry = getattr(launcher, "_require_geometry_submodule", None)
    validate = getattr(launcher, "_validate", None)
    if not callable(geometry) or not callable(validate):
        raise RuntimeError("Incompatible LicketyFit launcher: validation hooks are unavailable")
    analysis_tools = Path(checkout) / "analysis_tools" / "analysis_tools" / "data_loader.py"
    if not analysis_tools.is_file():
        raise RuntimeError("LicketyFit analysis_tools submodule is missing: %s" % analysis_tools)
    launcher.FIT_PARTICLE = particle
    launcher.ENERGY_LABEL_MEV = float(energy)
    launcher.SEEDING_MODE = seeding
    launcher.N_EVENTS = int(events)
    launcher.NPROC = 1
    launcher.MAX_INTERNAL_THREADS_PER_WORKER = int(cpus)
    launcher.USE_TRUTH_ROOT = False
    launcher.USE_THROUGHGOING = False
    geometry()
    validate(check_paths=False)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_output(checkout, arguments):
    try:
        result = subprocess.run(["git", "-C", checkout, *arguments], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def publish(input_path, result_path, output_path, fingerprint, events, expected_input_hash,
            checkout, datatools, particle, energy, seeding, cpus, sources):
    output = Path(output_path)
    marker = Path(str(output) + ".json")
    actual_input_hash = sha256(input_path)
    if actual_input_hash != expected_input_hash:
        raise RuntimeError("WCSim input changed while LicketyFit was running")
    try:
        with open(str(input_path) + ".production.json") as stream:
            input_origin = json.load(stream).get("origin", "unverified")
    except (OSError, ValueError, AttributeError):
        input_origin = "unverified"
    metadata = {
        "input_sha256": actual_input_hash,
        "input_origin": input_origin,
        "output_sha256": sha256(result_path),
        "fingerprint": fingerprint,
        "events": int(events),
        "configuration": {
            "checkout": checkout,
            "checkout_revision": git_output(checkout, ["rev-parse", "HEAD"]),
            "submodules": git_output(checkout, ["submodule", "status", "Geometry", "analysis_tools"]),
            "datatools": datatools,
            "source_sha256": sources,
            "particle": particle,
            "energy_mev": float(energy),
            "seeding": seeding,
            "workers": 1,
            "internal_threads": int(cpus),
        },
        "python": {"executable": sys.executable, "version": sys.version},
    }
    metadata["python"]["packages"] = {}
    for package in ("numpy", "numba", "scipy", "uproot", "awkward", "matplotlib", "pandas", "iminuit"):
        try:
            metadata["python"]["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            metadata["python"]["packages"][package] = None
    temporary = []
    try:
        for target in (output, marker):
            descriptor, path = tempfile.mkstemp(prefix=".licketyfit-", dir=target.parent)
            temporary.append(path)
            with os.fdopen(descriptor, "wb") as stream:
                if target == output:
                    with open(result_path, "rb") as source:
                        shutil.copyfileobj(source, stream)
                else:
                    stream.write((json.dumps(metadata, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
        os.link(temporary[0], output)
        os.link(temporary[1], marker)
    finally:
        for path in temporary:
            os.unlink(path)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--preflight":
        if len(sys.argv) != 8:
            raise ValueError("--preflight requires checkout, particle, energy, seeding, events and CPUs")
        preflight(*sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--publish":
        if len(sys.argv) != 15:
            raise ValueError("--publish requires input, result, fingerprint, configuration and source details")
        publish(*sys.argv[2:])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("events", type=int)
    parser.add_argument("particle")
    parser.add_argument("energy", type=float)
    parser.add_argument("seeding")
    parser.add_argument("cpus", type=int)
    args = parser.parse_args()

    with np.load(args.input, allow_pickle=True) as archive:
        arrays = [archive[name] for name in ("digi_hit_pmt", "digi_hit_charge", "digi_hit_time")]
        if any(len(array) != args.events for array in arrays):
            raise ValueError("NPZ event counts do not match the requested event count")
        if any(len(ids) != len(charges) or len(ids) != len(times)
               for ids, charges, times in zip(*arrays)):
            raise ValueError("NPZ hit arrays have inconsistent lengths")

    launcher = load_launcher(args.checkout)
    settings = {
        "INPUT_FILE": str(Path(args.input).resolve()),
        "OUTPUT_FILE": str(Path(args.output).resolve()),
        "N_EVENTS": args.events,
        "EVENT_START_INDEX": 0,
        "FIT_PARTICLE": args.particle,
        "ENERGY_LABEL_MEV": args.energy,
        "SEEDING_MODE": args.seeding,
        "NPROC": 1,
        "MAX_INTERNAL_THREADS_PER_WORKER": args.cpus,
        "USE_TRUTH_ROOT": False,
        "USE_THROUGHGOING": False,
    }
    for key, value in settings.items():
        setattr(launcher, key, value)
    for key in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    os.environ["OMP_NUM_THREADS"] = str(args.cpus)
    sys.argv = [sys.argv[0]]
    launcher.main()
    with open(args.output, "rb") as stream:
        result = pickle.load(stream)
    metadata = result.get("metadata", {})
    if metadata.get("n_events_completed") != args.events or metadata.get("n_events_requested") != args.events:
        raise RuntimeError("LicketyFit output is incomplete")


if __name__ == "__main__":
    main()