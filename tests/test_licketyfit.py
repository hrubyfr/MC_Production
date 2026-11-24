import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import shutil
import json
import importlib.util
from types import SimpleNamespace
from unittest import mock

import numpy as np

import licketyfit_production
from runSimulation import FileGenerator, JobSubmitter, SimulationConfig


class LicketyFitConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("fit/scripts/run_wcsim.py", "data/root_utils/event_dump.py"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        python = root / "python"
        python.touch(mode=0o755)
        self.config = SimulationConfig()
        self.config.siffile = "/example/container.sif"
        self.config.licketyfitdir = str(root / "fit")
        self.config.datatools = str(root / "data")
        self.config.licketyfit_python = str(python)
        self.config.lf_particle = "muon"
        self.config.lf_energy_mev = 100.0

    def test_valid_config(self):
        self.config.validate_licketyfit()

    def test_preflight_rejects_nonworking_python_and_incompatible_launcher(self):
        with self.assertRaisesRegex(ValueError, "failed preflight"):
            self.config.preflight_licketyfit()
        self.config.licketyfit_python = sys.executable
        with self.assertRaisesRegex(ValueError, "Incompatible LicketyFit launcher"):
            self.config.preflight_licketyfit()

    def test_preflight_success_never_runs_a_fit(self):
        self.config.licketyfit_python = sys.executable
        success = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("runSimulation.subprocess.run", return_value=success) as run:
            self.config.preflight_licketyfit()
        self.assertIn("--preflight", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_cli_preflight_fails_before_generation(self):
        root = Path(self.temp.name) / "preflight-cli"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        for name in ("runSimulation.py", "licketyfit_production.py"):
            shutil.copy(project / name, root)
        environment = dict(os.environ, SOFTWARE_SIF_FILE="/unused.sif", PYTHONDONTWRITEBYTECODE="1")
        command = [sys.executable, str(root / "runSimulation.py"), "--licketyfit", self.config.licketyfitdir,
                   "--licketyfit-python", sys.executable, "--datatools", self.config.datatools,
                   "--lf-particle", "muon", "--lf-energy-mev", "100", "--dry-run"]
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preflight failed", result.stderr)
        self.assertFalse((root / "mac").exists())

    def test_cli_preflight_and_condor_dry_run(self):
        root = Path(self.temp.name) / "preflight-cli-success"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        for name in ("runSimulation.py", "licketyfit_production.py"):
            shutil.copy(project / name, root)
        checkout = root / "fit"
        scripts = checkout / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "run_wcsim.py").write_text(
            "INPUT_FILE = OUTPUT_FILE = FIT_PARTICLE = SEEDING_MODE = ''\n"
            "N_EVENTS = EVENT_START_INDEX = ENERGY_LABEL_MEV = NPROC = MAX_INTERNAL_THREADS_PER_WORKER = 0\n"
            "USE_TRUTH_ROOT = USE_THROUGHGOING = False\n"
            "def main(): raise RuntimeError('fit must not run')\n"
            "def _require_geometry_submodule(): pass\n"
            "def _validate(*, check_paths): assert not check_paths\n"
        )
        analysis = checkout / "analysis_tools" / "analysis_tools" / "data_loader.py"
        analysis.parent.mkdir(parents=True)
        analysis.touch()
        dependencies = root / "dependencies"
        dependencies.mkdir()
        for name in ("numba", "scipy", "uproot", "awkward", "matplotlib", "pandas", "iminuit"):
            (dependencies / (name + ".py")).touch()
        environment = dict(os.environ, SOFTWARE_SIF_FILE="/unused.sif", PYTHONDONTWRITEBYTECODE="1",
                           PYTHONPATH=str(dependencies), PATH=str(root / "no-scheduler"))
        command = [sys.executable, str(root / "runSimulation.py"), "-n", "1", "-f", "1",
                   "--licketyfit", str(checkout), "--licketyfit-python", sys.executable,
                   "--datatools", self.config.datatools, "--lf-particle", "muon", "--lf-energy-mev", "100",
                   "--condor", "tomorrow", "--lf-condor-schedd", "test-schedd", "--dry-run"]
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dry-run complete: no jobs submitted", result.stdout)
        self.assertEqual(len(list((root / "mac").glob("*.mac"))), 2)
        self.assertEqual(len(list((root / "condor_dir").glob("*.sub"))), 1)

    def test_invalid_settings_are_rejected(self):
        for key, value in (("datatools", None), ("licketyfit_python", None),
                           ("lf_particle", "electron"), ("lf_energy_mev", float("nan")),
                           ("lf_cpus", 0), ("lf_memory_mb", 0), ("lf_walltime", "1:99:00"),
                           ("lf_walltime", "0:00:00"), ("submit_sukap_jobs", True)):
            with self.subTest(key=key):
                original = getattr(self.config, key)
                setattr(self.config, key, value)
                with self.assertRaises(ValueError):
                    self.config.validate_licketyfit()
                setattr(self.config, key, original)

    def test_existing_generated_file_is_not_overwritten(self):
        path = Path(self.temp.name) / "generated.mac"
        generator = FileGenerator(self.config)
        generator.write_generated(str(path), "first\n")
        generator.write_generated(str(path), "first\n")
        with self.assertRaisesRegex(ValueError, "different content"):
            generator.write_generated(str(path), "changed\n")
        self.assertEqual(path.read_text(), "first\n")

    def test_legacy_dry_run_does_not_overwrite_generated_files(self):
        self.config.licketyfitdir = None
        self.config.dry_run = True
        generator = FileGenerator(self.config)
        path = Path(self.temp.name) / "existing"
        path.write_text("original\n")
        with self.assertRaisesRegex(ValueError, "different content"):
            generator.write_generated(str(path), "replacement\n")
        self.assertEqual(path.read_text(), "original\n")

    def test_legacy_dry_run_preserves_each_generated_file(self):
        root = Path(self.temp.name) / "dry run preservation"
        root.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1] / "template", root / "template")
        self.config.curdir = str(root)
        self.config.licketyfitdir = None
        self.config.dry_run = True
        self.config.nfiles = 1
        self.config.submit_sukap_jobs = self.config.submit_cedar_jobs = self.config.submit_condor_jobs = True
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        submitter = JobSubmitter(self.config, generator)
        suffix = self.config.get_config_string() + "0000"
        cases = ((Path("mac/wcsim" + suffix + ".mac"), generator.generate_mac_files),
                 (Path("shell/run" + suffix + ".sh"), generator.generate_shell_scripts),
                 (Path("pjdir/pjsub" + suffix + ".sh"), submitter.submit_sukap),
                 (Path("sldir/slurm" + suffix + ".sh"), submitter.submit_cedar),
                 (Path("condor_dir/condor" + suffix + ".sub"), submitter.submit_condor))
        for path, generate in cases:
            with self.subTest(path=path):
                path.write_text("original\n")
                with self.assertRaisesRegex(ValueError, "different content"):
                    generate()
                self.assertEqual(path.read_text(), "original\n")

    def test_duplicate_simulation_seeds_are_redrawn(self):
        root = Path(self.temp.name) / "seed test"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 2
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        with mock.patch("runSimulation.random.Random.randrange", side_effect=[10, 10, 20]):
            generator.generate_mac_files()
        first = Path("mac/wcsim%s0000.mac" % self.config.get_config_string()).read_text()
        second = Path("mac/wcsim%s0001.mac" % self.config.get_config_string()).read_text()
        self.assertIn("/WCSim/random/seed 10\n", first)
        self.assertIn("/WCSim/random/seed 20\n", second)

    def test_different_productions_have_different_seeds(self):
        root = Path(self.temp.name) / "production seeds"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        generator.generate_mac_files()
        first = next(Path("mac").glob("wcsim*.mac")).read_text()
        self.config.ParticleKE = 200
        generator.generate_mac_files()
        second = next(path for path in Path("mac").glob("wcsim*.mac") if "200MeV" in path.name).read_text()
        import re
        seed = r"/WCSim/random/seed (\d+)"
        self.assertNotEqual(re.search(seed, first).group(1), re.search(seed, second).group(1))

    def test_seed_collision_with_existing_production_is_rejected(self):
        root = Path(self.temp.name) / "seed collision"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        Path("mac/wcsim_other.mac").write_text("/WCSim/random/seed 10\n")
        with mock.patch("runSimulation.random.Random.randrange", return_value=10):
            with self.assertRaisesRegex(ValueError, "conflicts with existing"):
                generator.generate_mac_files()

    def test_worker_generation_is_stable_and_shell_is_valid(self):
        root = Path(self.temp.name) / "production with spaces"
        root.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1] / "template", root / "template")
        shutil.copy(Path(__file__).resolve().parents[1] / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        generator.generate_mac_files()
        generator.generate_shell_scripts()
        pipeline = Path(generator.lf_shell_file(0))
        fit_script = Path(str(pipeline) + ".fit.sh")
        first = pipeline.read_bytes(), fit_script.read_bytes()
        for script in (pipeline, fit_script):
            subprocess.run(["bash", "-n", str(script)], check=True)
        generator.generate_mac_files()
        generator.generate_shell_scripts()
        self.assertEqual(first, (pipeline.read_bytes(), fit_script.read_bytes()))

    def test_expanding_production_preserves_existing_jobs(self):
        root = Path(self.temp.name) / "expansion"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        generator.generate_mac_files()
        generator.generate_shell_scripts()
        original_macro = next(Path("mac").glob("wcsim*.mac"))
        original_worker = Path(generator.lf_shell_file(0))
        original = (original_macro.read_bytes(), original_worker.read_bytes(), generator.stage_fingerprint)
        self.config.nfiles = 2
        expanded = FileGenerator(self.config)
        expanded.generate_mac_files()
        expanded.generate_shell_scripts()
        self.assertEqual(original, (original_macro.read_bytes(), original_worker.read_bytes(), expanded.stage_fingerprint))
        self.assertTrue(Path(expanded.lf_shell_file(1)).is_file())

    def test_backend_generation_dry_run_and_mock_submission(self):
        for backend in ("cedar", "condor"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = Path(__file__).resolve().parents[1]
                shutil.copytree(project / "template", root / "template")
                shutil.copy(project / "licketyfit_production.py", root)
                previous = os.getcwd()
                os.chdir(root)
                try:
                    config = self.config
                    config.curdir = str(root)
                    config.nfiles = 1
                    config.submit_cedar_jobs = backend == "cedar"
                    config.submit_condor_jobs = backend == "condor"
                    config.rapaccount = "account"
                    config.lf_condor_schedd = "schedd.example"
                    config.lf_cpus = 2
                    config.dry_run = True
                    config.validate_licketyfit()
                    generator = FileGenerator(config)
                    generator.create_directories()
                    generator.generate_mac_files()
                    generator.generate_shell_scripts()
                    submitter = JobSubmitter(config, generator)
                    submit = submitter.submit_cedar if backend == "cedar" else submitter.submit_condor
                    with mock.patch("licketyfit_production.subprocess.run", side_effect=AssertionError("scheduler contacted")):
                        submit()
                    folder = root / (generator.sldir if backend == "cedar" else generator.condordir)
                    batch = next(folder.iterdir())
                    contents = batch.read_text()
                    self.assertIn("--mem=16000M" if backend == "cedar" else "request_memory = 5120MB", contents)
                    self.assertIn("--cpus-per-task=2" if backend == "cedar" else "request_cpus = 2", contents)
                    config.dry_run = False
                    response = "Submitted batch job 42" if backend == "cedar" else "1 job(s) submitted to cluster 42."
                    with mock.patch("licketyfit_production.subprocess.run", return_value=subprocess.CompletedProcess([], 0, response, "")) as run:
                        submit()
                        self.assertEqual(run.call_count, 1)
                        with mock.patch("licketyfit_production.scheduler_terminal", return_value=False):
                            submit()
                        self.assertEqual(run.call_count, 1)
                finally:
                    os.chdir(previous)

    def test_registered_reconstruction_uses_shared_pipeline(self):
        root = Path(self.temp.name) / "registered stage"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        (root / "template" / "test_stage.sh").write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            "$python -c 'import hashlib,json,pathlib,sys; source,out,fp,events=sys.argv[1:]; "
            "pathlib.Path(out).write_bytes(b\"fit\"); "
            "digest=lambda path: hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest(); "
            "pathlib.Path(out+\".json\").write_text(json.dumps({\"input_sha256\":digest(source),"
            "\"output_sha256\":digest(out),\"fingerprint\":fp,\"events\":int(events)}))' "
            "$input $output $fingerprint $events\n"
        )
        self.config.curdir = str(root)
        self.config.licketyfitdir = None
        self.config.synthetic_enabled = True
        self.config.synthetic_python = sys.executable
        self.config.synthetic_memory = 6000
        self.config.synthetic_cpus = 2
        self.config.synthetic_walltime = "01:00:00"
        self.config.synthetic_schedd = None
        self.config.submit_cedar_jobs = True
        self.config.rapaccount = "account"
        self.config.nfiles = 1
        self.config.nevs = 1
        self.config.dry_run = True
        self.config.runWCSim = self.config.runMDT = self.config.runFQ = False
        self.config.adopt_unverified = True
        stage = licketyfit_production.StageDefinition(
            "synthetic", "synthetic_enabled", "mdt", "lf/synthetic{config}{index:04d}_{fingerprint}.dict",
            True, "template/test_stage.sh", lambda config: "source", lambda config, source: "a" * 64,
            lambda config: {"python": config.synthetic_python}, "synthetic_python", "synthetic_memory",
            "synthetic_cpus", "synthetic_walltime", "synthetic_schedd")
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        with mock.patch.object(licketyfit_production, "STAGES", licketyfit_production.STAGES + (stage,)):
            generator = FileGenerator(self.config)
            generator.create_directories()
            generator.generate_mac_files()
            generator.generate_shell_scripts()
            pipeline = Path(generator.lf_shell_file(0))
            script = Path(str(pipeline) + ".fit.sh")
            self.assertIn("pathlib.Path(out)", script.read_text())
            self.assertIn("mdt", pipeline.read_text())
            subprocess.run(["bash", "-n", str(pipeline)], check=True)
            suffix = self.config.get_config_string() + "0000.root"
            wcsim = root / "out" / ("wcsim" + suffix)
            mdt = root / "out" / ("mdt" + suffix)
            wcsim.write_bytes(b"mock WCSim")
            mdt.write_bytes(b"mock MDT")
            bindir = root / "bin"
            bindir.mkdir()
            fake_container = bindir / "apptainer"
            fake_container.write_text("#!/bin/sh\nexit 0\n")
            fake_container.chmod(0o755)
            environment = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
            result = subprocess.run(["bash", str(pipeline)], env=environment, capture_output=True, text=True)
            log = (root / "log" / ("run%s0000.log" % self.config.get_config_string())).read_text()
            self.assertEqual(result.returncode, 0, log + result.stderr)
            self.assertTrue(licketyfit_production.completed(mdt, root / generator.lf_output(0), generator.lf_fingerprint, 1))
            (root / generator.lf_output(0)).unlink()
            Path(str(root / generator.lf_output(0)) + ".json").unlink()
            with mock.patch("licketyfit_production.subprocess.run", side_effect=AssertionError("scheduler contacted")):
                JobSubmitter(self.config, generator).submit_cedar()
            batch = next(Path("sldir").iterdir()).read_text()
            self.assertIn("--cpus-per-task=2", batch)

    def test_stage_completion_is_independent_and_stale_is_rejected(self):
        root = Path(self.temp.name) / "production"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        submitter = JobSubmitter(self.config, generator)
        suffix = self.config.get_config_string() + "0000.root"
        wcsim = root / "out" / ("wcsim" + suffix)
        for name in ("wcsim", "mdt", "fq"):
            (root / "out" / (name + suffix)).write_bytes(name.encode())
        with self.assertRaisesRegex(ValueError, "Unrecorded"):
            submitter.is_job_missing(0)
        self.config.adopt_unverified = True
        self.assertTrue(submitter.is_job_missing(0))
        output = root / generator.lf_output(0)
        output.write_bytes(b"fit")
        Path(str(output) + ".json").write_text(json.dumps({
            "input_sha256": licketyfit_production.file_hash(wcsim),
            "output_sha256": licketyfit_production.file_hash(output),
            "fingerprint": generator.lf_fingerprint, "events": self.config.nevs,
        }))
        mdt = root / "out" / ("mdt" + suffix)
        fq = root / "out" / ("fq" + suffix)
        licketyfit_production.record_stage(wcsim, None, self.config.nevs, generator.stage_fingerprint, "generated")
        licketyfit_production.record_stage(mdt, wcsim, self.config.nevs, generator.stage_fingerprint, "generated")
        licketyfit_production.record_stage(fq, mdt, self.config.nevs, generator.stage_fingerprint, "generated")
        self.assertFalse(submitter.is_job_missing(0))
        fq.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "fq validation record is stale"):
            submitter.is_job_missing(0)
        fq.unlink()
        Path(str(fq) + ".production.json").unlink()
        self.assertTrue(submitter.is_job_missing(0))
        wcsim.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "stale"):
            submitter.is_job_missing(0)

    def test_stage_record_must_identify_its_origin(self):
        root = Path(self.temp.name)
        output = root / "legacy.root"
        output.write_bytes(b"legacy")
        marker = Path(str(output) + ".production.json")
        marker.write_text(json.dumps(licketyfit_production.stage_record(output, None, 1, "fingerprint")))
        self.assertFalse(licketyfit_production.stage_completed(output, None, 1, "fingerprint"))
        marker.unlink()
        licketyfit_production.record_stage(output, None, 1, "fingerprint", "adopted-unverified")
        self.assertTrue(licketyfit_production.stage_completed(output, None, 1, "fingerprint"))
        self.assertEqual(json.loads(marker.read_text())["origin"], "adopted-unverified")

    def test_disabled_wcsim_cannot_skip_stale_completed_fit(self):
        root = Path(self.temp.name) / "disabled stale input"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = self.config.nevs = 1
        self.config.runWCSim = self.config.runMDT = self.config.runFQ = False
        self.config.ParticleKE = 100.1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        original = FileGenerator(self.config)
        original.create_directories()
        wcsim = root / "out" / ("wcsim%s0000.root" % self.config.get_config_string())
        wcsim.write_bytes(b"old input")
        licketyfit_production.record_stage(wcsim, None, 1, original.stage_fingerprint, "generated")
        output = root / original.lf_output(0)
        output.write_bytes(b"fit")
        Path(str(output) + ".json").write_text(json.dumps({
            "input_sha256": licketyfit_production.file_hash(wcsim),
            "output_sha256": licketyfit_production.file_hash(output),
            "fingerprint": original.lf_fingerprint, "events": 1,
        }))
        self.assertFalse(JobSubmitter(self.config, original).is_job_missing(0))
        self.config.ParticleKE = 100.4
        changed = FileGenerator(self.config)
        self.assertEqual(original.lf_output(0), changed.lf_output(0))
        with self.assertRaisesRegex(ValueError, "input validation record is stale"):
            JobSubmitter(self.config, changed).is_job_missing(0)

    def test_legacy_job_requires_explicit_confirmation(self):
        root = Path(self.temp.name) / "legacy"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        Path(generator.condordir).mkdir()
        Path(generator.condordir, "condor%s0000.sub" % self.config.get_config_string()).touch()
        submitter = JobSubmitter(self.config, generator)
        with self.assertRaisesRegex(ValueError, "Legacy job"):
            submitter.is_job_missing(0)
        self.config.confirm_legacy_idle = True
        self.assertTrue(submitter.is_job_missing(0))

    def test_disabled_fitter_generates_no_fitter_files(self):
        root = Path(self.temp.name) / "legacy generation"
        root.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1] / "template", root / "template")
        self.config.curdir = str(root)
        self.config.licketyfitdir = None
        self.config.nfiles = 1
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        generator.generate_mac_files()
        generator.generate_shell_scripts()
        self.assertFalse((root / "lf").exists())
        self.assertEqual(len(list((root / "shell").iterdir())), 1)
        self.assertNotIn("licketyfit", next((root / "shell").iterdir()).read_text())

    def test_legacy_dry_run_never_contacts_scheduler(self):
        root = Path(self.temp.name) / "legacy dry run"
        root.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1] / "template", root / "template")
        self.config.curdir = str(root)
        self.config.licketyfitdir = None
        self.config.nfiles = 1
        self.config.dry_run = True
        self.config.submit_sukap_jobs = self.config.submit_cedar_jobs = self.config.submit_condor_jobs = True
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        submitter = JobSubmitter(self.config, generator)
        with mock.patch("runSimulation.subprocess.Popen", side_effect=AssertionError("scheduler contacted")):
            submitter.submit_sukap()
            submitter.submit_cedar()
            submitter.submit_condor()

    def test_fitter_source_change_changes_fingerprint(self):
        root = Path(self.temp.name) / "fingerprint"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        first = FileGenerator(self.config).lf_fingerprint
        launcher = Path(self.config.licketyfitdir) / "scripts" / "run_wcsim.py"
        launcher.write_text("changed = True\n")
        second = FileGenerator(self.config).lf_fingerprint
        self.assertNotEqual(first, second)

    def test_partial_submission_failure_reports_index(self):
        root = Path(self.temp.name) / "partial"
        root.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1] / "template", root / "template")
        shutil.copy(Path(__file__).resolve().parents[1] / "licketyfit_production.py", root)
        self.config.curdir = str(root)
        self.config.nfiles = 2
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        submitter = JobSubmitter(self.config, FileGenerator(self.config))
        with mock.patch.object(submitter, "submit_reconstruction_index", side_effect=[RuntimeError("failure"), True]) as submit:
            with self.assertRaisesRegex(RuntimeError, "0000: failure"):
                submitter.submit_reconstruction("cedar")
            self.assertEqual(submit.call_count, 2)

    def test_worker_converts_and_runs_fitter_outside_container(self):
        root = Path(self.temp.name) / "worker project"
        root.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / "template", root / "template")
        shutil.copy(project / "licketyfit_production.py", root)
        launcher = Path(self.config.licketyfitdir) / "scripts" / "run_wcsim.py"
        launcher.write_text(
            "import pickle\n"
            "INPUT_FILE = OUTPUT_FILE = FIT_PARTICLE = SEEDING_MODE = ''\n"
            "N_EVENTS = EVENT_START_INDEX = ENERGY_LABEL_MEV = NPROC = MAX_INTERNAL_THREADS_PER_WORKER = 0\n"
            "USE_TRUTH_ROOT = USE_THROUGHGOING = True\n"
            "def main():\n"
            "    with open(OUTPUT_FILE, 'wb') as stream:\n"
            "        pickle.dump({'metadata': {'n_events_requested': N_EVENTS, 'n_events_completed': N_EVENTS}}, stream)\n"
        )
        bindir = root / "bin"
        bindir.mkdir()
        executable = bindir / "apptainer"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import os, sys\n"
            "if any('event_dump.py' in arg for arg in sys.argv):\n"
            "    import numpy as np\n"
            "    bind = next(arg for arg in sys.argv if arg.endswith(':/scratch'))\n"
            "    output_dir = bind[:-len(':/scratch')]\n"
            "    basename = os.path.splitext(sys.argv[-2])[0]\n"
            "    np.savez(os.path.join(output_dir, basename + '.npz'), digi_hit_pmt=[[1]], digi_hit_charge=[[1.]], digi_hit_time=[[2.]])\n"
        )
        executable.chmod(0o755)
        self.config.curdir = str(root)
        self.config.nfiles = self.config.nevs = 1
        self.config.licketyfit_python = sys.executable
        self.config.runWCSim = self.config.runMDT = self.config.runFQ = False
        self.config.adopt_unverified = True
        self.config.submit_cedar_jobs = True
        self.config.rapaccount = "account"
        previous = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        generator = FileGenerator(self.config)
        generator.create_directories()
        generator.generate_mac_files()
        generator.generate_shell_scripts()
        generator.generate_shell_scripts()
        wcsim = root / "out" / ("wcsim%s0000.root" % self.config.get_config_string())
        wcsim.write_bytes(b"mock WCSim output")
        environment = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""))
        worker = generator.lf_shell_file(0)
        result = subprocess.run(["bash", worker], env=environment, capture_output=True, text=True)
        log = (root / "log" / ("run%s0000.log" % self.config.get_config_string())).read_text()
        self.assertEqual(result.returncode, 0, log + result.stderr)
        output = root / generator.lf_output(0)
        self.assertTrue(licketyfit_production.completed(wcsim, output, generator.lf_fingerprint, 1))
        self.assertEqual(json.loads(Path(str(output) + ".json").read_text())["input_origin"], "adopted-unverified")
        self.assertEqual(subprocess.run(["bash", worker], env=environment).returncode, 0)


class LicketyFitAdapterTests(unittest.TestCase):
    def test_preflight_checks_launcher_without_fitting(self):
        adapter_path = Path(__file__).resolve().parents[1] / "template" / "licketyfit.py"
        spec = importlib.util.spec_from_file_location("review_adapter", adapter_path)
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        launcher = SimpleNamespace(_require_geometry_submodule=mock.Mock(), _validate=mock.Mock(), main=mock.Mock())
        with tempfile.TemporaryDirectory() as directory:
            analysis = Path(directory) / "analysis_tools" / "analysis_tools" / "data_loader.py"
            analysis.parent.mkdir(parents=True)
            analysis.touch()
            with mock.patch.object(adapter, "load_launcher", return_value=launcher):
                with mock.patch.object(adapter.importlib, "import_module", return_value=object()):
                    adapter.preflight(directory, "muon", "100", "general", "3", "2")
        launcher._require_geometry_submodule.assert_called_once_with()
        launcher._validate.assert_called_once_with(check_paths=False)
        launcher.main.assert_not_called()
        self.assertEqual((launcher.FIT_PARTICLE, launcher.N_EVENTS, launcher.NPROC), ("muon", 3, 1))

    def test_adapter_configures_launcher_and_checks_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "fit" / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "run_wcsim.py").write_text(
                "import pickle\n"
                "INPUT_FILE = OUTPUT_FILE = FIT_PARTICLE = SEEDING_MODE = ''\n"
                "N_EVENTS = EVENT_START_INDEX = ENERGY_LABEL_MEV = NPROC = MAX_INTERNAL_THREADS_PER_WORKER = 0\n"
                "USE_TRUTH_ROOT = USE_THROUGHGOING = True\n"
                "def main():\n"
                "    assert FIT_PARTICLE == 'muon' and NPROC == 1 and MAX_INTERNAL_THREADS_PER_WORKER == 2\n"
                "    assert not USE_TRUTH_ROOT and not USE_THROUGHGOING\n"
                "    with open(OUTPUT_FILE, 'wb') as stream:\n"
                "        pickle.dump({'metadata': {'n_events_requested': N_EVENTS, 'n_events_completed': N_EVENTS}}, stream)\n"
            )
            input_path = root / "events.npz"
            np.savez(input_path, digi_hit_pmt=[[1]], digi_hit_charge=[[1.0]], digi_hit_time=[[2.0]])
            adapter = Path(__file__).resolve().parents[1] / "template" / "licketyfit.py"
            command = [sys.executable, str(adapter), str(root / "fit"), str(input_path),
                       str(root / "out.dict"), "1", "muon", "100", "general", "2"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "out.dict").is_file())
            command[5] = "2"
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("event counts", result.stderr)

    def test_publish_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.root"
            result_path = root / "result.dict"
            output_path = root / "final.dict"
            input_path.write_bytes(b"input")
            result_path.write_bytes(b"result")
            adapter = Path(__file__).resolve().parents[1] / "template" / "licketyfit.py"
            command = [sys.executable, str(adapter), "--publish", str(input_path),
                       str(result_path), str(output_path), "config-hash", "1", licketyfit_production.file_hash(input_path),
                       str(root), str(root), "muon", "100", "general", "1", "source-hash"]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(output_path.read_bytes(), b"result")
            self.assertTrue(Path(str(output_path) + ".json").is_file())
            metadata = json.loads(Path(str(output_path) + ".json").read_text())
            self.assertEqual(metadata["configuration"]["particle"], "muon")
            self.assertEqual(metadata["configuration"]["source_sha256"], "source-hash")
            self.assertTrue(licketyfit_production.completed(input_path, output_path, "config-hash", 1))
            self.assertFalse(licketyfit_production.completed(input_path, output_path, "other-hash", 1))
            input_path.write_bytes(b"changed")
            self.assertFalse(licketyfit_production.completed(input_path, output_path, "config-hash", 1))
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output_path.read_bytes(), b"result")


class SubmissionLedgerTests(unittest.TestCase):
    def test_completion_during_reconciliation_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "job.json"
            licketyfit_production.write_state(state, {"phase": "submitted", "backend": "cedar", "job_id": "42"})
            completed = []
            def finish(*args):
                completed.append(True)
                return True
            with mock.patch("licketyfit_production.scheduler_terminal", side_effect=finish):
                with mock.patch("licketyfit_production.subprocess.run", side_effect=AssertionError("duplicate submission")):
                    result = licketyfit_production.submit_once(
                        state, "cedar", ["sbatch", "job.sh"], is_complete=lambda: bool(completed))
            self.assertFalse(result)
            self.assertEqual(json.loads(state.read_text())["job_id"], "42")

    def test_condor_checks_the_recorded_schedd(self):
        empty = subprocess.CompletedProcess([], 0, "", "")
        complete = subprocess.CompletedProcess([], 0, "4\n", "")
        with mock.patch("licketyfit_production.subprocess.run", side_effect=[empty, complete]) as run:
            self.assertTrue(licketyfit_production.scheduler_terminal("condor", "42", "schedd.example"))
            self.assertEqual(run.call_args_list[0].args[0][:4], ["condor_q", "-name", "schedd.example", "42"])
            self.assertEqual(run.call_args_list[1].args[0][:4], ["condor_history", "-name", "schedd.example", "42"])

    def test_active_terminal_and_unknown_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "job.json"
            success = subprocess.CompletedProcess([], 0, "Submitted batch job 42\n", "")
            with mock.patch("licketyfit_production.subprocess.run", return_value=success) as run:
                self.assertTrue(licketyfit_production.submit_once(state, "cedar", ["sbatch", "job.sh"]))
                self.assertEqual(run.call_count, 1)
                with mock.patch("licketyfit_production.scheduler_terminal", return_value=False):
                    self.assertFalse(licketyfit_production.submit_once(state, "cedar", ["sbatch", "job.sh"]))
                with mock.patch("licketyfit_production.scheduler_terminal", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                        licketyfit_production.submit_once(state, "cedar", ["sbatch", "job.sh"])
                self.assertEqual(run.call_count, 1)
                with mock.patch("licketyfit_production.scheduler_terminal", return_value=True):
                    self.assertTrue(licketyfit_production.submit_once(state, "cedar", ["sbatch", "job.sh"]))
                self.assertEqual(run.call_count, 2)

    def test_ambiguous_submission_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "job.json"
            failure = subprocess.CompletedProcess([], 1, "", "submission failed")
            with mock.patch("licketyfit_production.subprocess.run", return_value=failure) as run:
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                        licketyfit_production.submit_once(state, "condor", ["condor_submit", "job.sub"])
                self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()