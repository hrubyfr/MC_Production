# MC_Production

Scripts and tools for WCTE Monte Carlo production using software containers. This repository provides a command-line interface and a web application to configure, generate, and submit simulation jobs.

## Prerequisites

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/WCTE/MC_Production.git
    cd MC_Production
    ```

2.  **Obtain the Software Container:**
    Follow instructions at [WCTE/SoftwareContainer](https://github.com/WCTE/SoftwareContainer) to get the Singularity Image File (.sif).

## Environment Setup

Before running any scripts, you must configure the environment variables using `setup.sh`. This script must be **sourced**.

### Basic Setup (SIF only)
```bash
source setup.sh /path/to/softwarecontainer.sif
```

### Sandbox Setup (Required for Sukap)
If you are submitting jobs to Sukap, a sandbox directory is required.

```bash
# If sandbox already exists
source setup.sh /path/to/softwarecontainer.sif /path/to/sandbox_dir

# To build the sandbox from the SIF (first time setup)
source setup.sh /path/to/softwarecontainer.sif /path/to/sandbox_dir --build
```

## Simulation CLI (`runSimulation.py`)

The main script `runSimulation.py` handles the generation of macros, shell scripts, and batch job submissions for the WCSim -> MDT -> fiTQun workflow.

### Usage
```bash
python3 runSimulation.py [options]
```

### Options

| Option | Long Option | Description |
| :--- | :--- | :--- |
| `-h` | `--help` | Print help message. |
| `-p` | `--pid` | Particle name (e.g., `mu-`, `e-`). Default: `mu-`. |
| `-b` | `--beam` | **Beam Mode**: `KE,WallDistance`. KE in MeV, distance from vertex to blacksheet in cm. |
| | `--realistic-beam` | **Realistic Beam Mode**: `KE,WallDistance,EnergySigma,BeamSigma` (MeV, cm, MeV, cm). Sets the Gaussian energy spread and equal X/Y beam sigmas; beam-pipe diameter is 12 cm. |
| `-u` | `--uniform` | **Uniform Mode**: `KE_Low,KE_High`. Random vertices with uniform KE in MeV. |
| `-m` | `--cosmics` | **Cosmics Mode**: Generate cosmic muon events. |
| | `--save-primary-truth` | Save primary-particle truth in the WCSim output. |
| | `--save-all-truth` | Save all-particle truth in the WCSim output. |
| `-n` | `--nevs` | Number of events per file. Default: 1000. |
| `-f` | `--nfiles` | Number of files to generate. Default: 100. |
| `-s` | `--seed` | RNG seed. Default: 20260129. |
| `-c` | `--cds` | Disable CDS in WCSim. |
| | `--wcsim` | **Disable** WCSim execution step. |
| | `--mdt` | **Disable** MDT execution step. |
| | `--fq` | **Disable** fiTQun execution step. |
| | `--licketyfit PATH` | Enable LicketyFit using the specified checkout on the worker host. |
| | `--licketyfit-python PATH` | Host Python executable with LicketyFit dependencies installed. Required with `--licketyfit`. |
| | `--datatools PATH` | DataTools checkout containing `root_utils/event_dump.py`. Required with `--licketyfit`. |
| | `--lf-particle` | Required fit hypothesis: `muon`, `pion`, `kaon`, or `proton`. |
| | `--lf-energy-mev` | Required non-negative sample energy label in MeV. |
| | `--lf-seeding` | `general` (default) or `beam`. |
| | `--lf-cpus` | Internal threads for one fit process. Default: 1. |
| | `--lf-memory-mb` | Memory request in MB. Default: 5120. |
| | `--lf-walltime` | Requested walltime in `HH:MM:SS`. Default: `24:00:00`. |
| | `--lf-condor-schedd` | HTCondor schedd name. Required for LicketyFit Condor jobs. |
| | `--confirm-legacy-idle` | Confirm matching legacy jobs have been checked and are no longer active. |
| | `--adopt-unverified` | Explicitly adopt existing ROOT files without provenance records as unverified. |
| | `--dry-run` | Generate batch files and show planned submissions without contacting a scheduler. |
| `-k` | `--sukap` | Submit batch jobs to **Sukap** (Requires Sandbox). Optional agrument: queue name (default: all).|
| `-d` | `--cedar` | Submit batch jobs to **Cedar** with specified RAP account. |
| | `--condor` | Submit batch jobs to **HTCondor** (LXPLUS). Optional agrument: JobFlavour (default: tomorrow)|

### Examples

**1. Generate scripts only (Beam mode):**
Generates 10 files for 100 MeV muons, 30cm from the wall.
```bash
python3 runSimulation.py -p mu- -b 100,30 -n 1000 -f 10
```

**2. Generate a realistic beam with both truth records:**
The beam uses a 900 MeV nominal kinetic energy, 30 cm wall distance, 19.9 MeV energy sigma, and 3.2 cm transverse beam sigma.
```bash
python3 runSimulation.py -p mu- --realistic-beam 900,30,19.9,3.2 --save-primary-truth --save-all-truth -n 1000 -f 10
```

**3. Submit jobs to Sukap:**
Requires `SOFTWARE_SANDBOX_DIR` to be set via `setup.sh`.
```bash 
python3 runSimulation.py -p e- -u 10,50 -n 1000 -f 100 -k
```

**4. Submit to Cedar:**
```bash
python3 runSimulation.py -m -n 1000 -f 50 -d def-myaccount
```

**5. Submit to HTCondor (LXPLUS):**
```bash 
python3 runSimulation.py -p mu+ -b 200,0 -n 1000 -f 20 --condor
```

### LicketyFit

LicketyFit is opt-in and reconstructs the WCSim ROOT file's built-in digitized
hits, independently of MDT and fiTQun. Each worker validates the WCSim event
count and converts that file to NPZ with DataTools inside the container. It then
runs the supplied LicketyFit checkout using the supplied **host** Python, outside
the container. The checkout must expose the `scripts/run_wcsim.py` launcher
interface; its Geometry and analysis_tools submodules must be initialized.
Before generating files, the selected host Python imports the fitter and its
dependencies, checks its launcher interface and geometry, and validates the fit
settings without running a fit. The same Python and checkout must also work on
the worker; submit-host preflight cannot verify worker-node access.

Install the checkout's Python requirements in a host environment (Python 3.10
or newer); its native backend also needs a C++17/OpenMP compiler. The production
directory, checkout, Python environment, DataTools checkout and container image
must be visible on every worker at the supplied paths. DataTools must be usable
with the container's ROOT and WCSim environment. Workers also need writable
scratch/cache storage; completed outputs are published using hard links, so the
production filesystem must support hard links and advisory file locks.

For example, after sourcing `setup.sh`:

```bash
python3 runSimulation.py -p mu- -b 100,0 -n 100 -f 2 \
    --licketyfit /shared/path/LicketyFit --licketyfit-python /shared/path/venv/bin/python \
    --datatools /shared/path/DataTools --lf-particle muon --lf-energy-mev 100 \
    --condor tomorrow --lf-condor-schedd YOUR_SCHEDD --dry-run
```

Remove `--dry-run` only when ready to submit. For CEDAR, replace the Condor
options with `--cedar ACCOUNT`. To fit existing WCSim output without running
the other stages, add `--wcsim --mdt --fq`; these options disable the named
stages. Add `--adopt-unverified` only after independently checking the provenance
of any unrecorded ROOT inputs. LicketyFit submission on Sukap is disabled until its resource directives
and job-status handling are verified. The web interface does not expose
LicketyFit options.

The default 5120 MB request is a starting point, not a measured upper bound:
conversion and other stages may require more. CEDAR requests at least its
existing 16000 MB whenever upstream stages are enabled. Increase memory,
CPUs or walltime after benchmarking representative inputs; one fit process
uses at most `--lf-cpus` internal threads. For Condor, choose a JobFlavour at
least as long as `--lf-walltime`; the flavour sets the actual limit. Cold-cache
compilation can increase startup time and memory.

Results are stored in `lf/` as `.dict` files with JSON completion records
containing hashes, configuration, source revision and Python package versions.
Enabled ROOT stages also get `.production.json` validation records alongside
their outputs. Unrecorded ROOT files are refused unless `--adopt-unverified` is
supplied. Such inputs are checked on a worker and marked `adopted-unverified`;
the fit record retains this origin. The check verifies the ROOT tree and event
count but cannot prove a legacy file's original physics configuration. A stale
record must be inspected before moving it aside; adopting the file after that
marks its provenance unverified rather than certifying its original settings.
Only load trusted `.dict` files: they use pickle. Existing partial or stale
results are not overwritten. Inspect and move both the result and its JSON
record before retrying. Submission ledgers in `lf/` prevent automatic
resubmission of active jobs and block ambiguous outcomes. If reconciliation is
unavailable, inspect the scheduler and ledger manually; move a ledger aside
only after confirming the corresponding job cannot still run. Legacy batch
files have no ledger: check all schedulers before using `--confirm-legacy-idle`.
For reconstruction-enabled productions, WCSim macro seeds are generated from
`--seed` and the physical simulation configuration, with duplicate draws
redrawn. Existing WCSim macros in the same production directory are checked for
collisions; separate directories or historical outputs without macros cannot be
checked automatically. Old macros are never overwritten when their seeds do
not match the new scheme. Increasing `--nfiles` preserves earlier job seeds.
At most one registered additional reconstruction can be selected per job;
it remains independent of the existing fiTQun option.

Additional reconstruction algorithms are registered in the shared stage list
with an enable/configuration attribute, prerequisite, output pattern, worker
template, source and configuration fingerprint functions, preflight hook, and
resource attributes. The template must publish its output with a `.json`
completion record containing the input and output SHA-256 values, fingerprint,
and event count. The shared dispatcher validates the named WCSim, MDT, or
fiTQun prerequisite and uses the same scheduler and retry handling. New user
options still need to populate the stage's configuration attributes.

## Web Application

A FastAPI-based web interface is available to configure simulations, submit jobs, and monitor status.

### Prerequisites
```bash
pip install -r requirements.txt
```

### Running the Server
Ensure `setup.sh` is sourced, then start the server:
```bash 
uvicorn main:app --host 127.0.0.1 --port 8080
```
If running on a remote cluster, use SSH tunneling to access it locally:
```bash
ssh -L 8080:127.0.0.1:8080 user@remote_server
```
Access at: `http://localhost:8080`

### Features 

- **Configuration**: Form-based setup for particle type, energy, and mode.
- **Submission**: Background task submission to Sukap, Cedar, or Condor.
- **Monitoring**: View active job status (wraps `pjstat`, `squeue`, `condor_q`).
- **Control**: Kill running jobs via the interface.

## Validation Tools

Root macros located in `validation/` can be run using the container.

The Python tests use NumPy for NPZ fixtures and mock all scheduler submissions:

```bash
python3 -m pip install numpy
python3 -m unittest discover -v
```

### MergeOutputFiles
Merge all indexed WCSim event files for a configuration suffix into one ROOT file. The geometry (`wcsimGeoT`) and options (`wcsimRootOptionsT`) trees are copied once from the first input. `AllSecondaries` is merged when present in any input.

```bash
python3 mergeOutputFiles.py '_wCDS_mu-_RealisticBeam_680MeV_0cm_Esigma10MeV_Bsigma2cm__PrimaryTruth'
```

The default output is `out/wcsim<suffix>_merged.root`. The input directory and output path can be changed with `--input-dir` and `--output`. To use a custom WCSim build, pass the build directory containing `this_wcsim.sh` with `-w`:

```bash
python3 mergeOutputFiles.py '_wCDS_mu-_RealisticBeam_680MeV_0cm_Esigma10MeV_Bsigma2cm__PrimaryTruth' -w /path/to/WCSim-build/install
```

The runner uses the software container configured by `setup.sh`, binds the repository at `/mnt`, sources `/opt/entrypoint.sh`, and then sources the selected WCSim setup script.

### EventDisplay.c
Aggregates events from files to produce PMT hit histograms (charges and times) in `fig/`.
```bash
singularity exec -B ./:/mnt $SOFTWARE_SIF_FILE root -l -b -q /mnt/validation/EventDisplay.c\(\"/mnt/out/\*.root\"\)
```

### EventDisplay_SingleEvent.c
Produces event display plots for a specific event ID.
```bash
singularity exec -B ./:/mnt $SOFTWARE_SIF_FILE root -l -b -q /mnt/validation/EventDisplay_SingleEvent.c\(\"/mnt/out/*.root\",evtID\)
```

### EventDisplay_Compare.c
Compares two sets of output files, calculating ratios of histograms.
```bash
singularity exec -B ./:/mnt $SOFTWARE_SIF_FILE root -l -b -q /mnt/validation/EventDisplay_Compare.c\(\"/mnt/out/[files1].root\",\"/mnt/out/[files2].root\"\,\"tag\"\)
```

### VertexDistribution.c
Plots the vertex distribution of all events.
```bash
singularity exec -B ./:/mnt $SOFTWARE_SIF_FILE root -l -b -q /mnt/validation/VertexDistribution.c\(\"/mnt/out/*.root\"\)
```

## Technical Documentation

### Code Structure

- **`runSimulation.py`**: The core orchestration script.
    - `SimulationConfig`: Stores configuration parameters (physics, file counts, toggles).
    - `FileGenerator`: Creates the directory structure (`mac/`, `shell/`, `out/`, etc.) and generates WCSim macros and shell execution scripts based on templates.
    - `JobSubmitter`: Handles the logic for submitting jobs to different batch systems (Sukap/pjsub, Cedar/Slurm, LXPLUS/Condor). It checks for existing output files to avoid re-running completed jobs.
    - `JobStatus`: (Used by Web App) Parses command line output from batch system tools to track or kill jobs.

- **`main.py`**: FastAPI application serving the web interface.
    - `submit_simulation`: Handles POST requests from the form, maps inputs to `SimulationConfig`, and triggers background job submission.
    - `get_job_status`: Queries the batch system status via `JobStatus`.

- **`setup.sh`**: Bash script to export necessary environment variables (`SOFTWARE_SIF_FILE`, `SOFTWARE_SANDBOX_DIR`) and optionally build the Singularity sandbox.

### Extending the Simulation

To enable more extensive simulation setups (e.g., changing physics lists, modifying geometry parameters, or using complex particle sources), follow these steps:

1.  **Modify the Template (`template/WCTE.mac`)**:
    This file contains the WCSim macro commands. You can add new commands found in the WCSim Repository.
    *   *Example*: To control the dark rate dynamically, change `/DarkRate/SetDarkRate 1.0 kHz` to `/DarkRate/SetDarkRate $darkRate kHz`.

2.  **Update `SimulationConfig` (`runSimulation.py`)**:
    Add a new member variable in the `__init__` method to store the configuration value.
    ```python
    self.darkRate = 1.0 # Default value
    ```

3.  **Update `FileGenerator` (`runSimulation.py`)**:
    In the `generate_mac_files` method, update the `macTemplate.substitute(...)` call to include the new variable mapping.
    ```python
    fo.write(macTemplate.substitute(
        ...
        darkRate=self.cfg.darkRate,
        ...
    ))
    ```

4.  **Update CLI (`runSimulation.py`)**:
    Add the new argument to the `argparse` setup in `main()` (e.g., `parser.add_argument(...)`) to allow users to set it via command line.

#### Example: Particle Generation
The default template uses the Geant4 General Particle Source (GPS).
```bash
/mygen/generator gps
/gps/particle $ParticleName
```
The `$ParticleName` variable is replaced by the `-p` argument (default: `mu-`). You can use any particle name supported by Geant4 (e.g., `e-`, `gamma`, `pi0`, `proton`, `opticalphoton`).

To use a different generator (e.g., for supernova events or laser calibration), you would modify `template/WCTE.mac` to change `/mygen/generator` and add the relevant configuration commands, potentially creating new variables for the `FileGenerator` to populate.

### Extending the Web Interface

To expose new configuration options (added to `SimulationConfig` as described above) in the web interface:

1.  **Update `templates/index.html`**:
    Add a new HTML input field inside the configuration form. Ensure the `name` attribute matches the argument name you will use in `main.py`.
    ```html
    <div class="form-group">
        <label for="dark_rate">Dark Rate (kHz):</label>
        <input type="number" class="form-control" id="dark_rate" name="dark_rate" value="1.0" step="0.1">
    </div>
    ```

2.  **Update `main.py`**:
    Modify the `submit_simulation` function to accept the new form field as an argument and assign it to the configuration object.
    ```python
    @app.post("/submit")
    async def submit_simulation(
        # ... existing arguments ...
        dark_rate: float = Form(1.0), # Add this line
    ):
        # ...
        config = runSimulation.SimulationConfig()
        # ...
        config.darkRate = dark_rate # Map to config
        # ...
    ```

### Output Directory Structure
- `mac/`: Generated WCSim macros.
- `shell/`: Generated execution shell scripts.
- `out/`: Root output files (WCSim, MDT, fiTQun).
- `log/`: Execution logs.
- `fig/`: Validation plots.
- `pjdir/`, `sldir/`, `condor_dir/`: Batch submission scripts.
- `pjout/`, `slout/`, `condor_out/`: Batch system standard output.

## DataTools
WatChMaL provides a python package to convert WCSim root output into numpy array.
```bash
git clone https://github.com/WatChMaL/DataTools
singularity run -B ./:/mnt $SOFTWARE_SIF_FILE bash
source /opt/WCSim/build/this_wcsim.sh
cd /mnt/DataTools
export DATATOOLS=`pwd`
python $DATATOOLS/root_utils/event_dump.py root_file -d out_dir # reads root_file and produces .npz file in out_dir
# Geometry file, only need one 
python $DATATOOLS/root_utils/full_geo_dump.py root_file geo_file_name.npz
```
To read the .npz file in python,
```
import numpy as np
npz_file = np.load(file_name, allow_pickle=True)
# check $DATATOOLS/root_utils/event_dump.py, root_file_utils.py for availale variables
hit_time = npz_file['digi_hit_time'] # this gets the pmt digi hit time
```