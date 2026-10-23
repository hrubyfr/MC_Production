#!/usr/bin/env python3
"""Merge indexed WCSim ROOT outputs for one production template.

Pass either the configuration suffix or the full ``wcsim_...`` filename
prefix, without the four-digit index or ``.root`` extension. Matching inputs
are merged into ``out/<PREFIX>_merged.root`` by default. The ``wcsimT`` event trees are
combined, geometry and options trees are copied from the first input, and
``AllSecondaries`` is combined when present.

Usage::

    python3 mergeOutputFiles.py '_wCDS_mu-_RealisticBeam_680MeV_0cm_Esigma10MeV_Bsigma2cm__PrimaryTruth'
    python3 mergeOutputFiles.py 'wcsim_wCDS_mu-_RealisticBeam_680MeV_0cm_Esigma10MeV_Bsigma2cm__PrimaryTruth' --input-dir /path/to/out \
        --output /path/to/merged.root -w /path/to/WCSim-build/install

The container is taken from ``SOFTWARE_SANDBOX_DIR`` or
``SOFTWARE_SIF_FILE`` (or can be supplied with ``--container``). The runner
binds the repository at ``/mnt``, sources ``/opt/entrypoint.sh``, then sources
``this_wcsim.sh`` from the selected build directory. By default, the container
WCSim build is ``/opt/WCSim/build``.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parent
CONTAINER_REPOSITORY = Path("/mnt")
DEFAULT_WCSIM_BUILD_DIR = "/opt/WCSim/build"
DEFAULT_CONTAINER_BINDS = (
    "/afs",
    "/cvmfs",
    "/cvmfs/grid.cern.ch/etc/grid-security:/etc/grid-security",
    "/cvmfs/grid.cern.ch/etc/grid-security/vomses:/etc/vomses",
    "/eos",
    "/etc/pki/ca-trust",
    "/etc/tnsnames.ora",
    "/run/user",
    "/var/run/user",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Merge WCSim event files for a production configuration suffix."
    )
    parser.add_argument(
        "template",
        help="configuration suffix, e.g. _wCDS_mu-_RealisticBeam_680MeV_0cm_Esigma10MeV_Bsigma2cm__PrimaryTruth",
    )
    parser.add_argument("--input-dir", type=Path, default=REPOSITORY / "out")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--container",
        default=os.environ.get("SOFTWARE_SANDBOX_DIR") or os.environ.get("SOFTWARE_SIF_FILE"),
        help="software container image or sandbox (defaults to setup.sh environment)",
    )
    parser.add_argument(
        "-w",
        "--wcsimBuildDir",
        dest="wcsim_build_dir",
        help="custom WCSim build directory containing this_wcsim.sh",
    )
    return parser.parse_args()


def path_in_repository(path):
    try:
        return path.relative_to(REPOSITORY)
    except ValueError:
        return None


def container_path(path):
    path = path.resolve()
    relative_path = path_in_repository(path)
    if relative_path is not None:
        return CONTAINER_REPOSITORY / relative_path
    return path


def wcsim_prefix(template):
    return template if template.startswith("wcsim") else "wcsim" + template


def matching_input_files(input_dir, template):
    prefix = wcsim_prefix(template)
    suffix_pattern = re.compile(re.escape(prefix) + r"(\d{4})\.root$")
    matched_files = []
    for path in input_dir.glob("wcsim*.root"):
        match = suffix_pattern.fullmatch(path.name)
        if match:
            matched_files.append((int(match.group(1)), path.resolve()))
    return [path for _, path in sorted(matched_files)]


def existing_container_binds():
    binds = []
    for bind in DEFAULT_CONTAINER_BINDS:
        source = bind.split(":", 1)[0]
        if Path(source).exists():
            binds.append(bind)
    return binds


def root_string(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def run_merge(args):
    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise ValueError("Input directory does not exist: %s" % input_dir)

    input_files = matching_input_files(input_dir, args.template)
    if not input_files:
        raise ValueError(
            "No indexed WCSim files found for template %r in %s"
            % (args.template, input_dir)
        )

    output_file = args.output.expanduser().resolve() if args.output else (
        input_dir / (wcsim_prefix(args.template) + "_merged.root")
    )
    if output_file in input_files:
        raise ValueError("Output path must not be one of the input files")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    container = args.container
    if not container:
        raise ValueError(
            "No software container configured; source setup.sh or pass --container"
        )

    runtime = shutil.which("apptainer") or shutil.which("singularity")
    if not runtime:
        raise ValueError("Neither apptainer nor singularity is available on PATH")

    custom_build_dir = None
    if args.wcsim_build_dir:
        custom_build_dir = Path(args.wcsim_build_dir).expanduser().resolve()
        setup_script = custom_build_dir / "this_wcsim.sh"
        if not custom_build_dir.is_dir() or not setup_script.is_file():
            raise ValueError(
                "Custom WCSim build directory must contain this_wcsim.sh: %s"
                % custom_build_dir
            )
        build_script_in_container = str(container_path(setup_script))
    else:
        build_script_in_container = DEFAULT_WCSIM_BUILD_DIR + "/this_wcsim.sh"

    bindings = {str(REPOSITORY) + ":/mnt"}
    for directory in (input_dir, output_file.parent, custom_build_dir):
        if directory is not None and path_in_repository(directory) is None:
            bindings.add(str(directory) + ":" + str(directory))

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix=".merge-inputs-", suffix=".txt",
        dir=REPOSITORY, delete=False
    ) as input_list:
        input_list_path = Path(input_list.name)
        for path in input_files:
            input_list.write(str(container_path(path)) + "\n")

    try:
        macro_path = CONTAINER_REPOSITORY / "validation" / "MergeOutputFiles.cc"
        list_path = container_path(input_list_path)
        output_path = container_path(output_file)
        macro_call = "%s(%s, %s)" % (
            macro_path,
            root_string(list_path),
            root_string(output_path),
        )
        command = "source /opt/entrypoint.sh && source %s && root -l -b -q %s" % (
            shlex.quote(build_script_in_container),
            shlex.quote(macro_call),
        )

        environment = os.environ.copy()
        default_binds = ",".join(existing_container_binds())
        if default_binds:
            environment.setdefault("APPTAINER_BINDPATH", default_binds)
            environment.setdefault("SINGULARITY_BINDPATH", default_binds)

        invocation = [runtime, "exec"]
        for binding in sorted(bindings):
            invocation.extend(["--bind", binding])
        invocation.extend([container, "bash", "-lc", command])

        print("Merging %d files into %s" % (len(input_files), output_file))
        subprocess.run(invocation, check=True, env=environment)
        if not output_file.is_file() or output_file.stat().st_size == 0:
            raise RuntimeError("ROOT did not produce a non-empty output: %s" % output_file)
        print("Created %s" % output_file)
    finally:
        input_list_path.unlink(missing_ok=True)


def main():
    args = parse_args()
    try:
        run_merge(args)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print("Error: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())