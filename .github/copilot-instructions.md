# MC_Production – project instructions

MC_Production prepares and launches Monte Carlo productions for **WCSim**, a Geant4-based water Cherenkov detector simulation, and optionally the processing that follows it. Simulations never run locally; they run on a computing grid.

## What the tool does

1. **Generates files from template files**, for a supplied configuration:
   - **shell scripts** (worker-node job wrappers: environment setup, run the program, stage outputs),
   - **simulation macros** (`.mac`: geometry, physics, generator/particle gun, seed, `/run/beamOn`),
   - **tuning macros** (`.mac` files with the WCSim tuning parameters, passed to WCSim as an **additional command-line argument**, separate from the simulation macro).
2. **Skips work that already exists.** It checks whether the files generated for a configuration already exist; if they do, **no job is submitted** for it.
3. **Submits jobs** to one of three backends:
   - **sukap**,
   - the **CERN computing grid via HTCondor**,
   - **CEDAR**.
4. **Optional downstream stages**, run only when the user passes the corresponding arguments to the main script:
   - **Digitizer**: runs a digitization algorithm on the true hits from the simulation to produce a detector response closer to real data.
   - **FitQun**: a reconstruction algorithm that reconstructs the original vertex, direction and energy from the charge and time hits. More reconstruction algorithms may be added in future, so stages should be added through a common mechanism, not special-cased.

Stage chain: **simulation → (digitizer) → (reconstruction, e.g. FitQun)**. A downstream stage consumes the outputs of the previous one.

## Invariants for every change

- Same config → same generated files (deterministic, stable ordering).
- Every simulation job has a unique, reproducible random seed.
- Generated scripts must work on the **worker node of the chosen backend**, not just the local machine; no hardcoded absolute paths, usernames, hostnames or credentials. Backend-specific details (scheduler syntax, paths, environment setup, storage) live in backend-specific code/templates, not scattered through shared code.
- Everything is generated from templates. Change templates rather than hardcoding file contents in code; unfilled placeholders must be detected and rejected.
- Macro command order and units must be valid for the targeted WCSim version. Never invent macro commands; mark anything unverified. The tuning macro is a separate file passed as an extra argument to WCSim; keep it consistent with how WCSim is invoked in the shell template.
- Unknown or out-of-range config/tuning values are rejected with a clear error, not silently ignored.
- **Existence check**: if the files for a configuration already exist, do not regenerate over them and do not submit. The check must be reliable (it must not skip a job whose files are incomplete or stale, and must not resubmit a job that was already generated and submitted). Make skipping visible in the output.
- **Downstream stages are opt-in.** Without the relevant argument, no digitizer or reconstruction file is generated or job submitted. A downstream stage must be tied to the correct simulation outputs and must not run on missing or incomplete inputs.
- Generation and submission support a dry-run. Tests and development **never** perform a real submission to any backend.

## Workflow

Use the custom agents in this order: **MC Planner** → **MC Implementer** → **MC Reviewer**, using the handoff buttons between them.

<!-- TODO: fill in repo-specific details so the agents don't have to guess:
- Language / build tool and how to run tests and linters:
- Targeted WCSim version and where its source/docs live:
- Scheduler and submit/status commands for each backend (sukap, CERN HTCondor, CEDAR):
- Seed scheme (e.g. production id + job index):
- Config file format, template directory, and example configs:
- Main script name and the arguments that enable digitizer / FitQun:
- How the existence check decides a configuration is "already done" (which files, which paths):
-->
