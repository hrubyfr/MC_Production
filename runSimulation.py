#!/usr/bin/env python
import time
import sys
import argparse
import os
import math
import subprocess
import string
import random
import getpass
import re
import shlex
import hashlib
import json

import licketyfit_production

CONDOR_FLAVOURS = [
    "espresso",     # 20 minutes
    "microcentury", # 1 hour
    "longlunch",    # 2 hours
    "workday",      # 8 hours
    "tomorrow",     # 1 day
    "testmatch",    # 3 days
    "nextweek"      # 1 week
]

class SimulationConfig:
    def __init__(self):
        # Default parameters
        self.wcsimdir = "/opt/WCSim"
        self.geant4dir = "/opt/geant4"
        self.wcsim_build_dir = "/opt/WCSim/build"
        self.mntdir = "/mnt"
       # self.licketyfitdir: str = None
        
        self.curdir = os.getcwd()
        
        # Environment
        self.siffile = os.environ.get("SOFTWARE_SIF_FILE")
        self.sandbox = os.environ.get("SOFTWARE_SANDBOX_DIR")

        self.rngseed = 20260129
        self.ParticleName = "mu-"

        self.TankRadius = 307.5926/2
        self.TankHalfz = 271.4235/2

        self.nevs = 1000
        self.nfiles = 100
        self.useCDS = True
        self.runWCSim = True
        self.runMDT = True
        self.runFQ = True
        #self.runLF = False
        self.licketyfitdir = None
        self.licketyfit_python = None
        self.datatools = None
        self.lf_particle = None
        self.lf_energy_mev = None
        self.lf_seeding = "general"
        self.lf_memory_mb = 5120
        self.lf_cpus = 1
        self.lf_walltime = "24:00:00"
        self.lf_condor_schedd = None
        self.confirm_legacy_idle = False
        self.adopt_unverified = False
        self.dry_run = False
        
        self.submit_sukap_jobs = False
        self.submit_cedar_jobs = False
        self.submit_condor_jobs = False
        self.rapaccount = ""
        self.sukap_queue = "all"
        self.condor_queue = "tomorrow"

        self.useBeam = True
        self.ParticleKE = 100
        self.ParticleDirx = 0
        self.ParticleDiry = 0
        self.ParticleDirz = 1
        self.ParticlePosx = 0
        self.ParticlePosy = -42.47625
        self.wallD = 0.
        self.ParticlePosz = -(self.TankRadius - self.wallD)

        self.useRealisticBeam = False
        self.EnergySigma = 19.9
        self.BeamSigma = 3.2

        self.savePrimaryParticleTruth = False
        self.saveAllParticleTruth = False

        self.useUniform = False
        self.ParticleKELow = 0.
        self.ParticleKEHigh = 2000.

        self.useCosmics = False

    def validate(self):
        if not self.siffile and not self.sandbox:
            print ("ERROR: SOFTWARE_SIF_FILE and SOFTWARE_SANDBOX_DIR not set.")
            sys.exit(1)
        if self.submit_sukap_jobs and not self.sandbox:
            print ("ERROR: SOFTWARE_SANDBOX_DIR is needed for sukap submission.")
            sys.exit(1)
        reconstructions = licketyfit_production.enabled_reconstructions(self)
        if len(reconstructions) > 1:
            raise ValueError("Select only one registered reconstruction per job")
        if reconstructions and self.submit_sukap_jobs:
            raise ValueError("Registered reconstruction on Sukap is unavailable until resource and job-status handling are verified")
        for stage in reconstructions:
            if stage.validation_method:
                getattr(self, stage.validation_method)()

    def validate_licketyfit(self):
        if self.submit_sukap_jobs:
            raise ValueError("LicketyFit submission on Sukap is unavailable until resource and job-status handling are verified")
        if self.submit_condor_jobs and self.submit_cedar_jobs:
            raise ValueError("Select only one scheduler for LicketyFit")
        if self.submit_condor_jobs and (not self.lf_condor_schedd or not re.fullmatch(r"[A-Za-z0-9_.-]+", self.lf_condor_schedd)):
            raise ValueError("--lf-condor-schedd=%r must name the HTCondor schedd" % self.lf_condor_schedd)
        if self.submit_cedar_jobs and not re.fullmatch(r"[A-Za-z0-9_.-]+", self.rapaccount):
            raise ValueError("--cedar=%r must be a valid account" % self.rapaccount)
        if not self.siffile:
            raise ValueError("SOFTWARE_SIF_FILE is required for LicketyFit conversion")
        for key, path in (("production directory", self.curdir), ("--datatools", self.datatools)):
            if path and any(char in path for char in (":", ",", "\n")):
                raise ValueError("%s=%r contains characters unsupported by container bind paths" % (key, path))
        if not re.fullmatch(r"[A-Za-z0-9_+.-]+", self.ParticleName):
            raise ValueError("--pid=%r contains unsafe filename characters" % self.ParticleName)
        for key, path, relative in (
            ("--licketyfit", self.licketyfitdir, "scripts/run_wcsim.py"),
            ("--datatools", self.datatools, "root_utils/event_dump.py"),
        ):
            if not path or not os.path.isfile(os.path.join(path, relative)):
                raise ValueError("%s=%r must contain %s" % (key, path, relative))
        if not self.licketyfit_python or not os.path.isfile(self.licketyfit_python) or not os.access(self.licketyfit_python, os.X_OK):
            raise ValueError("--licketyfit-python=%r must be an executable file" % self.licketyfit_python)
        if self.lf_particle not in ("muon", "pion", "kaon", "proton"):
            raise ValueError("--lf-particle=%r must be muon, pion, kaon or proton" % self.lf_particle)
        if self.lf_energy_mev is None or not math.isfinite(self.lf_energy_mev) or self.lf_energy_mev < 0:
            raise ValueError("--lf-energy-mev=%r must be finite and non-negative" % self.lf_energy_mev)
        if self.lf_seeding not in ("general", "beam"):
            raise ValueError("--lf-seeding=%r must be general or beam" % self.lf_seeding)
        if self.nevs < 1 or self.nfiles < 1:
            raise ValueError("--nevs and --nfiles must be positive for LicketyFit")
        if self.nfiles > 1000000000:
            raise ValueError("--nfiles exceeds the available unique WCSim seeds")
        if self.lf_memory_mb < 1 or self.lf_cpus < 1:
            raise ValueError("--lf-memory-mb and --lf-cpus must be positive")
        if not re.fullmatch(r"\d+:[0-5]\d:[0-5]\d", self.lf_walltime) or not any(int(part) for part in self.lf_walltime.split(":")):
            raise ValueError("--lf-walltime=%r must be a positive HH:MM:SS duration" % self.lf_walltime)
        if self.submit_condor_jobs:
            hours, minutes, seconds = (int(part) for part in self.lf_walltime.split(":"))
            durations = dict(zip(CONDOR_FLAVOURS, (1200, 3600, 7200, 28800, 86400, 259200, 604800)))
            if hours * 3600 + minutes * 60 + seconds > durations[self.condor_queue]:
                raise ValueError("--lf-walltime exceeds the selected --condor JobFlavour")

    def preflight_licketyfit(self):
        adapter = os.path.join(self.curdir, "template", "licketyfit.py")
        command = [self.licketyfit_python, "-B", adapter, "--preflight", os.path.abspath(self.licketyfitdir),
                   self.lf_particle, str(self.lf_energy_mev), self.lf_seeding, str(self.nevs), str(self.lf_cpus)]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            result = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("--licketyfit-python=%r failed preflight: %s" % (self.licketyfit_python, exc)) from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
            raise ValueError("LicketyFit preflight failed for --licketyfit=%r and --licketyfit-python=%r: %s" %
                             (self.licketyfitdir, self.licketyfit_python, detail))

    def get_config_string(self):
        wCDSstring = "_wCDS" if self.useCDS else ""
        
        if self.useRealisticBeam:
            beamstring = "RealisticBeam_%gMeV_%gcm_Esigma%gMeV_Bsigma%gcm_" % (
                self.ParticleKE, self.wallD, self.EnergySigma, self.BeamSigma
            )
        else:
            beamstring = "Beam_%.0fMeV_%icm_" % (self.ParticleKE, int(self.wallD)) if self.useBeam else ""
        
        uniformstring = "Uniform_%.0f_%.0fMeV_" % (self.ParticleKELow, self.ParticleKEHigh) if self.useUniform else ""
        
        cosmicsstring = "Cosmics_" if self.useCosmics else ""

        configString = "%s_%s_%s%s" % (wCDSstring, self.ParticleName, beamstring, uniformstring)
        if self.useCosmics:
            configString = "%s_%s" % (wCDSstring, cosmicsstring)

        if self.savePrimaryParticleTruth:
            configString += "_PrimaryTruth"
        if self.saveAllParticleTruth:
            configString += "_AllParticleTruth"
        
        return configString

    def simulation_seed_identity(self):
        keys = ("rngseed", "ParticleName", "ParticleKE", "ParticleDirx", "ParticleDiry",
                "ParticleDirz", "ParticlePosx", "ParticlePosy", "ParticlePosz", "wallD",
                "useCDS", "useBeam", "useRealisticBeam", "EnergySigma", "BeamSigma",
                "useUniform", "ParticleKELow", "ParticleKEHigh", "useCosmics",
                "savePrimaryParticleTruth", "saveAllParticleTruth", "nevs", "TankRadius", "TankHalfz")
        return json.dumps({key: getattr(self, key) for key in keys}, sort_keys=True, separators=(',', ':'))

class FileGenerator:
    def __init__(self, config):
        self.cfg = config
        self.macdir = "mac"
        self.outdir = "out"
        self.logdir = "log"
        self.shelldir = "shell"
        #self.lfdir = "lf"
        self.figdir = "fig"
        self.pjdir = "pjdir"
        self.pjoutdir = "pjout"
        self.pjerrdir = "pjerr"
        self.sldir = "sldir"
        self.sloutdir = "slout"
        self.slerrdir = "slerr"
        self.condordir = "condor_dir"
        self.condorout = "condor_out"
        self.condorerr = "condor_err"
        self.condorlog = "condor_log"
        self.lfdir = "lf"
        self.lf_sources = None
        self.lf_fingerprint = None
        self.stage_fingerprint = None
        reconstructions = licketyfit_production.enabled_reconstructions(self.cfg)
        if len(reconstructions) > 1:
            raise ValueError("Select only one registered reconstruction per job")
        self.reconstruction_stage = reconstructions[0] if reconstructions else None
        if self.reconstruction_stage:
            self.lf_sources = self.reconstruction_stage.source_factory(self.cfg)
            self.lf_fingerprint = self.reconstruction_stage.fingerprint_factory(self.cfg, self.lf_sources)
            self.stage_fingerprint = licketyfit_production.stage_fingerprint(self.cfg)

    def write_generated(self, path, content):
        if not self.reconstruction_stage and not self.cfg.dry_run:
            with open(path, 'w') as output:
                output.write(content)
            return
        try:
            with open(path, 'x') as output:
                output.write(content)
        except FileExistsError:
            with open(path, 'r') as existing:
                if existing.read() != content:
                    raise ValueError("Generated file already exists with different content: %s" % path)

    def create_directories(self):
        dirs = [self.macdir, self.outdir, self.logdir, self.shelldir, self.figdir]
        if self.reconstruction_stage:
            dirs.append(self.lfdir)
        if self.cfg.submit_sukap_jobs:
            dirs.extend([self.pjdir, self.pjoutdir, self.pjerrdir])
        if self.cfg.submit_cedar_jobs:
            dirs.extend([self.sldir, self.sloutdir, self.slerrdir])
        if self.cfg.submit_condor_jobs:
            dirs.extend([self.condordir, self.condorout, self.condorerr, self.condorlog])

        for d in dirs:
            if not os.path.exists(d):
                os.makedirs(d)

    def generate_mac_files(self):
        if self.reconstruction_stage and not self.cfg.runWCSim:
            return
        print ("Creating mac files for WCSim")
        configString = self.cfg.get_config_string()
        
        wCDSmac = "" if self.cfg.useCDS else "#"
        beammac = "" if self.cfg.useBeam else "#"
        realisticBeamMac = "" if self.cfg.useRealisticBeam else "#"
        uniformmac = "" if self.cfg.useUniform else "#"
        comsicsmac = "" if self.cfg.useCosmics else "#"
        savePrimaryTruthMac = "" if self.cfg.savePrimaryParticleTruth else "#"
        saveAllTruthMac = "" if self.cfg.saveAllParticleTruth else "#"

        with open("template/WCTE.mac", 'r') as f:
            macTemplate = string.Template(f.read())
        with open("template/tuning_parameters.mac", 'r') as f:
            tuningTemplate = string.Template(f.read())

        if self.reconstruction_stage:
            seed_identity = self.cfg.simulation_seed_identity()
            seed_rng = random.Random(int.from_bytes(hashlib.sha256(seed_identity.encode()).digest(), 'big'))
            existing_seeds = {}
            for name in sorted(os.listdir(self.macdir)):
                if not name.startswith("wcsim") or not name.endswith(".mac"):
                    continue
                path = os.path.join(self.macdir, name)
                with open(path) as existing:
                    match = re.search(r"(?m)^\s*/WCSim/random/seed\s+(\d+)\s*$", existing.read())
                if match:
                    existing_seeds.setdefault(int(match.group(1)), set()).add(path)
        else:
            random.seed(self.cfg.rngseed)
            seed_rng = random
            existing_seeds = {}
        used_seeds = set()

        for i in range(self.cfg.nfiles):
            macFile = "%s/wcsim%s%04i.mac" % (self.macdir, configString, i)
            macseed = seed_rng.randrange(int(1e9))
            while self.reconstruction_stage and macseed in used_seeds:
                macseed = seed_rng.randrange(int(1e9))
            used_seeds.add(macseed)
            if self.reconstruction_stage and os.path.exists("%s/wcsim%s%04i.root" % (self.outdir, configString, i)):
                continue
            conflicts = existing_seeds.get(macseed, set()) - {macFile}
            if self.reconstruction_stage and conflicts:
                raise ValueError("WCSim seed %d for %s conflicts with existing macro(s): %s" %
                                 (macseed, macFile, ", ".join(sorted(conflicts))))
            
            self.write_generated(macFile, macTemplate.substitute(
                    wcsimdir=self.cfg.wcsimdir, 
                    rngseed=macseed, 
                    wCDSmac=wCDSmac, 
                    beammac=beammac,
                    realisticBeamMac=realisticBeamMac,
                    uniformmac=uniformmac,
                    comsicsmac=comsicsmac,
                    savePrimaryTruthMac=savePrimaryTruthMac,
                    saveAllTruthMac=saveAllTruthMac,
                    ParticleName=self.cfg.ParticleName,
                    ParticleKE=self.cfg.ParticleKE,
                    EnergySigma=self.cfg.EnergySigma,
                    BeamSigma=self.cfg.BeamSigma,
                    ParticleDirx=self.cfg.ParticleDirx,
                    ParticleDiry=self.cfg.ParticleDiry,
                    ParticleDirz=self.cfg.ParticleDirz,
                    ParticlePosx=self.cfg.ParticlePosx,
                    ParticlePosy=self.cfg.ParticlePosy,
                    ParticlePosz=self.cfg.ParticlePosz,
                    ParticleKELow=self.cfg.ParticleKELow,
                    ParticleKEHigh=self.cfg.ParticleKEHigh, 
                    rmac=self.cfg.TankRadius, 
                    zmac=self.cfg.TankHalfz,
                    nevs=self.cfg.nevs,
                    filename="%s/%s/wcsim%s%04i.root" % (self.cfg.mntdir, self.outdir, configString, i)
                ))

            tuningFile = "%s/tuning_parameters%s%04i.mac" % (self.macdir, configString, i)
            self.write_generated(tuningFile, tuningTemplate.substitute(wcsimdir=self.cfg.wcsimdir))

    def generate_shell_scripts(self):
        if self.reconstruction_stage:
            self.generate_reconstruction_scripts()
            return
        print ("Creating shell scripts for simulation")
        configString = self.cfg.get_config_string()
        
        cern_condor = "" if self.cfg.submit_condor_jobs else "#"
        userns = "-u" if self.cfg.submit_sukap_jobs else ""
        siffile = self.cfg.sandbox if self.cfg.submit_sukap_jobs else self.cfg.siffile
        
        runwcsim = "" if self.cfg.runWCSim else "#"
        runmdt = "" if self.cfg.runMDT else "#"
        runfq = "" if self.cfg.runFQ else "#"
        sourcePath = self.cfg.wcsim_build_dir+"/this_wcsim.sh"

        with open("template/run.sh", 'r') as f:
            shTemplate = string.Template(f.read())

        for i in range(self.cfg.nfiles):
            shFile = "%s/run%s%04i.sh" % (self.shelldir, configString, i)
            wcsimfile = "%s/%s/wcsim%s%04i.root" % (self.cfg.mntdir, self.outdir, configString, i)
            mdtfile = "%s/%s/mdt%s%04i.root" % (self.cfg.mntdir, self.outdir, configString, i)
            fqfile = "%s/%s/fq%s%04i.root" % (self.cfg.mntdir, self.outdir, configString, i)
            
            self.write_generated(shFile, shTemplate.substitute(
                    curdir=self.cfg.curdir,
                    cern_condor=cern_condor, 
                    userns=userns,
                    sourcePath = sourcePath,
                    mntdir=self.cfg.mntdir,
                    siffile=siffile,
                    runwcsim=runwcsim,
                    runmdt=runmdt,
                    runfq=runfq,
                    macfile="%s/%s/wcsim%s%04i.mac" % (self.cfg.mntdir, self.macdir, configString, i),
                    tuningfile="%s/%s/tuning_parameters%s%04i.mac" % (self.cfg.mntdir, self.macdir, configString, i),
                    logfile="%s/%s/run%s%04i.log" % (self.cfg.mntdir, self.logdir, configString, i),
                    wcsimfile=wcsimfile, 
                    nevs=self.cfg.nevs,
                    mdtfile=mdtfile,
                    fqfile=fqfile,
                    rngseed=random.randrange(int(1e9))
                ))

    def lf_output(self, index):
        return self.stage_output(self.reconstruction_stage, index)

    def stage_output(self, stage, index):
        return stage.output_pattern.format(config=self.cfg.get_config_string(), index=index,
                                           fingerprint=(self.lf_fingerprint or "")[:16])

    def lf_ledger(self, index):
        return os.path.join(self.lfdir, "job%s%04i.submission.json" % (
            self.cfg.get_config_string(), index))

    def lf_shell_file(self, index):
        stages = "%d%d%d" % (self.cfg.runWCSim, self.cfg.runMDT, self.cfg.runFQ)
        backend = "condor" if self.cfg.submit_condor_jobs else "cedar" if self.cfg.submit_cedar_jobs else "none"
        return os.path.join(self.shelldir, "run_%s%s%04i_%s_%s_%s_a%d.sh" % (
            self.reconstruction_stage.name, self.cfg.get_config_string(), index, self.lf_fingerprint[:16], stages, backend, self.cfg.adopt_unverified))

    def generate_reconstruction_scripts(self):
        stage = self.reconstruction_stage
        print("Creating %s worker scripts" % stage.name)
        with open(stage.worker_template) as stream:
            fit_template = string.Template(stream.read())
        with open("template/run_licketyfit_pipeline.sh") as stream:
            pipeline_template = string.Template(stream.read())
        with open("template/run_reconstruction_stage.sh") as stream:
            dispatch_template = string.Template(stream.read())
        quote = shlex.quote
        production = os.path.abspath(self.cfg.curdir)
        python = os.path.abspath(getattr(self.cfg, stage.python_attribute))
        stage_values = {key: quote(str(value)) for key, value in stage.worker_values(self.cfg).items()}
        container = "apptainer" if self.cfg.submit_condor_jobs or self.cfg.submit_cedar_jobs else "singularity"
        mdt_identity = "mdt:" + self.cfg.simulation_seed_identity()
        seeds = random.Random(int.from_bytes(hashlib.sha256(mdt_identity.encode()).digest(), 'big'))
        for index in range(self.cfg.nfiles):
            config = "%s%04i" % (self.cfg.get_config_string(), index)
            pipeline = self.lf_shell_file(index)
            fit_script = pipeline + ".fit.sh"
            prerequisite = next(item for item in licketyfit_production.STAGES if item.name == stage.prerequisite)
            input_path = os.path.join(production, self.stage_output(prerequisite, index))
            parent = next((item for item in licketyfit_production.STAGES if item.name == prerequisite.prerequisite), None)
            parent_input = os.path.join(production, self.stage_output(parent, index)) if parent else ""
            output_path = os.path.join(production, self.stage_output(stage, index))
            fit_values = dict(
                production=quote(production), image=quote(self.cfg.siffile), setup=quote(self.cfg.wcsim_build_dir + "/this_wcsim.sh"),
                input=quote(input_path), output=quote(output_path), events=self.cfg.nevs, container=quote(container), userns="",
                fingerprint=quote(self.lf_fingerprint), sources=quote(self.lf_sources))
            fit_values.update(stage_values)
            dispatch = dispatch_template.substitute(
                output=quote(output_path), marker=quote(output_path + ".json"), python=quote(python),
                helper=quote(os.path.join(production, "licketyfit_production.py")), input=quote(input_path),
                fingerprint=quote(self.lf_fingerprint), events=self.cfg.nevs,
                script=quote(os.path.join(production, fit_script)),
                input_name=quote(os.path.basename(input_path)), tree=quote(prerequisite.tree_name),
                origin='"$%s_origin"' % prerequisite.name, parent_input=quote(parent_input))
            dispatches = {name + "_reconstruction": "" for name in ("wcsim", "mdt", "fq")}
            if stage.prerequisite not in ("wcsim", "mdt", "fq"):
                raise ValueError("Unsupported reconstruction prerequisite: %s" % stage.prerequisite)
            dispatches[stage.prerequisite + "_reconstruction"] = dispatch
            pipeline_values = dict(
                production=quote(production), image=quote(self.cfg.siffile), setup=quote(self.cfg.wcsim_build_dir + "/this_wcsim.sh"),
                container=quote(container), userns="", config=quote(config), events=self.cfg.nevs,
                seed=seeds.randrange(int(1e9)), run_wcsim=int(self.cfg.runWCSim),
                run_mdt=int(self.cfg.runMDT), run_fq=int(self.cfg.runFQ),
                python=quote(python),
                stage_fingerprint=quote(self.stage_fingerprint),
                adopt_unverified=int(self.cfg.adopt_unverified),
                cern_condor="" if self.cfg.submit_condor_jobs else "#")
            pipeline_values.update(dispatches)
            self.write_generated(fit_script, fit_template.substitute(fit_values))
            self.write_generated(pipeline, pipeline_template.substitute(pipeline_values))

class JobSubmitter:
    def __init__(self, config, file_generator):
        self.cfg = config
        self.fgen = file_generator

    def is_job_missing(self, i):
        configString = self.cfg.get_config_string()
        lf_missing = False
        if self.fgen.reconstruction_stage:
            stages = {stage.name: stage for stage in licketyfit_production.STAGES}
            for stage in licketyfit_production.STAGES:
                if not getattr(self.cfg, stage.enabled_attribute):
                    continue
                if stage.prerequisite:
                    prerequisite = stages[stage.prerequisite]
                    input_path = self.fgen.stage_output(prerequisite, i)
                    if not getattr(self.cfg, prerequisite.enabled_attribute) and not os.path.isfile(input_path):
                        raise ValueError("Missing %s input for %s: %s" % (prerequisite.name, stage.name, input_path))
                    if os.path.isfile(input_path) and not os.path.exists(input_path + ".production.json") and not self.cfg.adopt_unverified:
                        raise ValueError("Unrecorded %s input %s; use --adopt-unverified only after checking its provenance" % (prerequisite.name, input_path))
                    if os.path.exists(input_path + ".production.json"):
                        parent = stages.get(prerequisite.prerequisite)
                        parent_path = self.fgen.stage_output(parent, i) if parent else None
                        if not licketyfit_production.stage_completed(input_path, parent_path, self.cfg.nevs, self.fgen.stage_fingerprint):
                            raise ValueError("%s input validation record is stale: %s" % (prerequisite.name, input_path))
                if stage.recorded_completion:
                    output = self.fgen.stage_output(stage, i)
                    if os.path.exists(output) or os.path.exists(output + ".json"):
                        if not licketyfit_production.completed(input_path, output, self.fgen.lf_fingerprint, self.cfg.nevs):
                            raise ValueError("%s output is incomplete or stale; inspect and move it before retrying: %s" % (stage.name, output))
                    else:
                        lf_missing = True
        if self.fgen.reconstruction_stage:
            upstream_missing = False
            stages = {stage.name: stage for stage in licketyfit_production.STAGES}
            for stage in licketyfit_production.STAGES:
                if stage.recorded_completion or not getattr(self.cfg, stage.enabled_attribute):
                    continue
                output = self.fgen.stage_output(stage, i)
                input_path = self.fgen.stage_output(stages[stage.prerequisite], i) if stage.prerequisite else None
                marker = output + ".production.json"
                complete = licketyfit_production.stage_completed(output, input_path, self.cfg.nevs, self.fgen.stage_fingerprint)
                if os.path.isfile(output) and not os.path.exists(marker) and not self.cfg.adopt_unverified:
                    raise ValueError("Unrecorded %s output %s; use --adopt-unverified only after checking its provenance" % (stage.name, output))
                if os.path.exists(marker) and not complete:
                    raise ValueError("%s validation record is stale; inspect and move it before retrying: %s" % (stage.name, marker))
                if not complete:
                    upstream_missing = True
        else:
            upstream_missing = any(
                enabled and not os.path.exists("%s/%s%s%04i.root" % (self.fgen.outdir, stage, configString, i))
                for enabled, stage in ((self.cfg.runWCSim, "wcsim"), (self.cfg.runMDT, "mdt"), (self.cfg.runFQ, "fq"))
            )
        if self.fgen.reconstruction_stage and upstream_missing and not self.cfg.confirm_legacy_idle:
            ledger = self.fgen.lf_ledger(i)
            candidates = (
                "%s/pjsub%s%04i.sh" % (self.fgen.pjdir, configString, i),
                "%s/slurm%s%04i.sh" % (self.fgen.sldir, configString, i),
                "%s/condor%s%04i.sub" % (self.fgen.condordir, configString, i),
            )
            if not os.path.exists(ledger) and any(os.path.exists(path) for path in candidates):
                raise ValueError("Legacy job files may represent an active job for index %04i; check all schedulers and use --confirm-legacy-idle only after confirming it is not active" % i)
        return upstream_missing or lf_missing

    def scan_jobs(self):
        n_to_submit = 0
        n_skipped = 0
        for i in range(self.cfg.nfiles):
            if self.is_job_missing(i):
                n_to_submit += 1
            else:
                n_skipped += 1
        return n_to_submit, n_skipped

    def submit_reconstruction(self, backend):
        template_path = "template/licketyfit_%s.%s" % ("slurm" if backend == "cedar" else "condor", "sh" if backend == "cedar" else "sub")
        with open(template_path) as stream:
            template = string.Template(stream.read())
        submitted = 0
        skipped = 0
        failures = []
        for index in range(self.cfg.nfiles):
            try:
                if self.submit_reconstruction_index(backend, template, index):
                    submitted += 1
                else:
                    skipped += 1
            except (OSError, ValueError, RuntimeError) as exc:
                failures.append("%04i: %s" % (index, exc))
        if self.cfg.dry_run:
            print("Dry-run complete: no jobs submitted.")
        else:
            print("Submitted %d jobs; skipped %d completed or active jobs." % (submitted, skipped))
        if failures:
            raise RuntimeError("Reconstruction job failures:\n" + "\n".join(failures))

    def submit_reconstruction_index(self, backend, template, index):
        if not self.is_job_missing(index):
            print("Skipping completed job %04i" % index)
            return False
        worker = self.fgen.lf_shell_file(index)
        name = os.path.splitext(os.path.basename(worker))[0]
        stage = self.fgen.reconstruction_stage
        memory = getattr(self.cfg, stage.memory_attribute)
        cpus = getattr(self.cfg, stage.cpus_attribute)
        requested_walltime = getattr(self.cfg, stage.walltime_attribute)
        schedd = getattr(self.cfg, stage.schedd_attribute)
        resource_values = (backend, memory, cpus, requested_walltime,
                   self.cfg.rapaccount, self.cfg.condor_queue, schedd)
        name += "_" + hashlib.sha256(repr(resource_values).encode()).hexdigest()[:12]
        if backend == "cedar":
            if self.cfg.runWCSim or self.cfg.runMDT or self.cfg.runFQ:
                memory = max(memory, 16000)
            hours, minutes, seconds = (int(part) for part in requested_walltime.split(":"))
            days, hours = divmod(hours, 24)
            walltime = "%d-%02d:%02d:%02d" % (days, hours, minutes, seconds)
            batch = os.path.join(self.fgen.sldir, name + ".sh")
            values = dict(account=self.cfg.rapaccount, walltime=walltime, memory=memory,
                          cpus=cpus, output=os.path.join(self.fgen.sloutdir, name),
                          error=os.path.join(self.fgen.slerrdir, name),
                          production=shlex.quote(self.cfg.curdir), worker=shlex.quote(worker))
            command = ["sbatch", batch]
        else:
            batch = os.path.join(self.fgen.condordir, name + ".sub")
            values = dict(worker=worker, cpus=cpus, memory=memory,
                          output=os.path.join(self.fgen.condorout, name),
                          error=os.path.join(self.fgen.condorerr, name),
                          log=os.path.join(self.fgen.condorlog, name), flavour=self.cfg.condor_queue)
            command = ["bash", "-lc", 'module load lxbatch/eossubmit && exec condor_submit -name "$1" "$2"',
                       "--", schedd, batch]
        self.fgen.write_generated(batch, template.substitute(values))
        ledger = self.fgen.lf_ledger(index)
        return licketyfit_production.submit_once(
            ledger, backend, command, self.cfg.dry_run, schedd,
            is_complete=lambda: not self.is_job_missing(index))

    def submit_licketyfit(self, backend):
        return self.submit_reconstruction(backend)

    def submit_licketyfit_index(self, backend, template, index):
        return self.submit_reconstruction_index(backend, template, index)

    def submit_sukap(self):
        if not self.cfg.submit_sukap_jobs: return

        configString = self.cfg.get_config_string()
        print ("Submitting pjsub jobs on sukap")
        
        with open("template/pjsub.sh", 'r') as f:
            shTemplate = string.Template(f.read())

        n_submitted = 0
        n_skipped = 0
        for i in range(self.cfg.nfiles):
            if self.is_job_missing(i):
                n_submitted += 1
                while not self.cfg.dry_run:
                    com = subprocess.Popen("pjstat | wc -l", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                    res, err = com.communicate()
                    try:
                        queueCount = int(res)
                    except:
                        queueCount = 9999
                    if queueCount < 300:
                        break
                    time.sleep(10)

                shFile = "%s/run%s%04i.sh" % (self.fgen.shelldir, configString, i)
                pjFile = "%s/pjsub%s%04i.sh" % (self.fgen.pjdir, configString, i)
                pjout = "%s/pjsub%s%04i.out" % (self.fgen.pjoutdir, configString, i)
                pjerr = "%s/pjsub%s%04i.err" % (self.fgen.pjerrdir, configString, i)
                
                self.fgen.write_generated(pjFile, shTemplate.substitute(
                        curdir=self.cfg.curdir,
                        shFile=shFile,
                        pjout=pjout,
                        pjerr=pjerr,
                        rscgrp=self.cfg.sukap_queue
                    ))

                if self.cfg.dry_run:
                    print("Dry-run: would submit %s" % pjFile)
                    continue
                com = subprocess.Popen("pjsub %s" % (pjFile), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                res, err = com.communicate()
                if len(err) > 0:
                    raise RuntimeError("Sukap submission failed: %s" % err.decode('utf-8'))
                else:
                    print (res.decode('utf-8'))
            else:
                n_skipped += 1
        print (("Dry-run: would submit %d jobs. Skipped %d existing jobs." if self.cfg.dry_run else "Submitted %d jobs. Skipped %d jobs due to existing files.") % (n_submitted, n_skipped))

    def submit_cedar(self):
        if not self.cfg.submit_cedar_jobs: return
        if self.fgen.reconstruction_stage:
            return self.submit_reconstruction("cedar")
        
        configString = self.cfg.get_config_string()
        print ("Creating slurm scripts for WCSim")
        
        with open("template/slurm.sh", 'r') as f:
            slTemplate = string.Template(f.read())
            
        siffile = self.cfg.siffile

        for i in range(self.cfg.nfiles):
            slFile = "%s/slurm%s%04i.sh" % (self.fgen.sldir, configString, i)
            slout = "%s/slurm%s%04i" % (self.fgen.sloutdir, configString, i)
            slerr = "%s/slurm%s%04i" % (self.fgen.slerrdir, configString, i)
            
            self.fgen.write_generated(slFile, slTemplate.substitute(
                    account=self.cfg.rapaccount, 
                    curdir=self.cfg.curdir, 
                    mntdir=self.cfg.mntdir, 
                    siffile=siffile, 
                    sout=slout, 
                    serr=slerr,
                    shFile="%s/run%s%04i.sh" % (self.fgen.shelldir, configString, i)
                ))

        print ("Submitting slurm jobs on cedar")
        n_submitted = 0
        n_skipped = 0
        for i in range(self.cfg.nfiles):
            if self.is_job_missing(i):
                n_submitted += 1
                slFile = "%s/slurm%s%04i.sh" % (self.fgen.sldir, configString, i)
                if self.cfg.dry_run:
                    print("Dry-run: would submit %s" % slFile)
                    continue
                com = subprocess.Popen("sbatch %s" % (slFile), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                res, err = com.communicate()
                if len(err) > 0:
                    raise RuntimeError("Cedar submission failed: %s" % err.decode('utf-8'))
                else:
                    print (res.decode('utf-8'))
            else:
                n_skipped += 1
        print (("Dry-run: would submit %d jobs. Skipped %d existing jobs." if self.cfg.dry_run else "Submitted %d jobs. Skipped %d jobs due to existing files.") % (n_submitted, n_skipped))

    def submit_condor(self):
        if not self.cfg.submit_condor_jobs: return
        if self.fgen.reconstruction_stage:
            return self.submit_reconstruction("condor")

        configString = self.cfg.get_config_string()
        print ("Creating condor scripts")
        
        with open("template/condor_submit.sub", 'r') as f:
            condorTemplate = string.Template(f.read())

        for i in range(self.cfg.nfiles):
            condorFile = "%s/condor%s%04i.sub" % (self.fgen.condordir, configString, i)
            out = "%s/condor%s%04i" % (self.fgen.condorout, configString, i)
            err = "%s/condor%s%04i" % (self.fgen.condorerr, configString, i)
            log = "%s/condor%s%04i" % (self.fgen.condorlog, configString, i)
            shfile = "%s/run%s%04i.sh" % (self.fgen.shelldir, configString, i)
            
            self.fgen.write_generated(condorFile, condorTemplate.substitute(shfile=shfile, out=out, err=err, log=log, JobFlavour=self.cfg.condor_queue))

        print ("Submitting condor jobs on lxplus")
        n_submitted = 0
        n_skipped = 0
        for i in range(self.cfg.nfiles):
            if self.is_job_missing(i):
                n_submitted += 1
                condorFile = "%s/condor%s%04i.sub" % (self.fgen.condordir, configString, i)
                if self.cfg.dry_run:
                    print("Dry-run: would submit %s" % condorFile)
                    continue
                com = subprocess.Popen("module load lxbatch/eossubmit && condor_submit %s" % (condorFile), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                res, err = com.communicate()
                if len(err) > 0:
                    raise RuntimeError("Condor submission failed: %s" % err.decode('utf-8'))
                else:
                    print (res.decode('utf-8'))
            else:
                n_skipped += 1
        print (("Dry-run: would submit %d jobs. Skipped %d existing jobs." if self.cfg.dry_run else "Submitted %d jobs. Skipped %d jobs due to existing files.") % (n_submitted, n_skipped))

class JobStatus:
    def __init__(self, config):
        self.cfg = config
        self.user = os.environ.get('USER')
        if not self.user:
            self.user = getpass.getuser()

    def get_jobs(self):
        jobs = {}
        if self.cfg.submit_sukap_jobs:
            jobs['sukap'] = self.get_sukap_jobs()
        if self.cfg.submit_cedar_jobs:
            jobs['cedar'] = self.get_cedar_jobs()
        if self.cfg.submit_condor_jobs:
            jobs['condor'] = self.get_condor_jobs()
        return jobs

    def get_sukap_jobs(self):
        jobs = []
        header = ""
        try:
            com = subprocess.Popen("pjstat", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            res, err = com.communicate()
            if res:
                lines = res.decode('utf-8').split('\n')
                for line in lines:
                    parts = line.split()
                    if len(parts) < 5: continue
                    if parts[0] == "JOB_ID":
                        header = line
                        continue
                    if parts[4] == self.user:
                        jobs.append(line)
        except Exception as e:
            print ("Error getting sukap jobs: %s" % str(e))
        
        if len(jobs) > 0 and header != "":
            jobs.insert(0, header)
        return jobs

    def get_cedar_jobs(self):
        jobs = []
        header = ""
        try:
            com = subprocess.Popen("squeue", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            res, err = com.communicate()
            if res:
                lines = res.decode('utf-8').split('\n')
                for line in lines:
                    parts = line.split()
                    if len(parts) < 4: continue
                    if parts[0] == "JOBID":
                        header = line
                        continue
                    if parts[3] == self.user:
                        jobs.append(line)
        except Exception as e:
            print ("Error getting cedar jobs: %s" % str(e))
            
        if len(jobs) > 0 and header != "":
            jobs.insert(0, header)
        return jobs

    def get_condor_jobs(self):
        jobs = []
        try:
            com = subprocess.Popen("condor_q -global", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            res, err = com.communicate()
            if res:
                lines = res.decode('utf-8').split('\n')
                current_schedd = ""
                current_header = ""
                current_jobs = []

                for line in lines:
                    if line.startswith("-- Schedd"):
                        if len(current_jobs) > 0:
                            jobs.append(current_schedd)
                            jobs.append(current_header)
                            jobs.extend(current_jobs)
                            jobs.append("")
                        current_schedd = line
                        current_header = ""
                        current_jobs = []
                    elif line.startswith("OWNER"):
                        current_header = line
                    else:
                        parts = line.split()
                        if len(parts) > 0 and parts[0] == self.user:
                            current_jobs.append(line)
                
                if len(current_jobs) > 0:
                    jobs.append(current_schedd)
                    jobs.append(current_header)
                    jobs.extend(current_jobs)

        except Exception as e:
            print ("Error getting condor jobs: %s" % str(e))
        return jobs

    def kill_jobs(self):
        if self.cfg.submit_sukap_jobs:
            self._kill_sukap_jobs()
        if self.cfg.submit_cedar_jobs:
            self._kill_cedar_jobs()
        if self.cfg.submit_condor_jobs:
            self._kill_condor_jobs()

    def _kill_sukap_jobs(self):
        print("Killing sukap jobs...")
        try:
            com = subprocess.Popen("pjstat", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            res, err = com.communicate()
            if res:
                lines = res.decode('utf-8').split('\n')
                for line in lines:
                    parts = line.split()
                    if len(parts) > 4 and parts[4] == self.user:
                        job_id = parts[0]
                        print(f"Killing sukap job {job_id}")
                        kill_com = subprocess.Popen(f"pjdel {job_id}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                        kill_res, kill_err = kill_com.communicate()
                        if kill_res:
                            print(kill_res.decode('utf-8'))
                        if kill_err:
                            print(kill_err.decode('utf-8'))
        except Exception as e:
            print(f"Error killing sukap jobs: {e}")

    def _kill_cedar_jobs(self):
        print(f"Killing cedar jobs for user {self.user}...")
        try:
            com = subprocess.Popen(f"scancel -u {self.user}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            res, err = com.communicate()
            if res:
                print(res.decode('utf-8'))
            if err:
                print(err.decode('utf-8'))
        except Exception as e:
            print(f"Error killing cedar jobs: {e}")

    def _kill_condor_jobs(self):
        print(f"Killing condor jobs for user {self.user}...")
        try:
            com = subprocess.Popen("condor_q -global", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            res, err = com.communicate()
            if res:
                lines = res.decode('utf-8').split('\n')
                schedds_with_user_jobs = set()
                current_schedd = None

                for line in lines:
                    if line.startswith("-- Schedd"):
                        # Example: -- Schedd: bigbird03.cern.ch (137.138.105.78) : <137.138.105.78:9618?...
                        parts = line.split()
                        if len(parts) > 2:
                            current_schedd = parts[2] # bigbird03.cern.ch
                    else:
                        job_parts = line.split()
                        if len(job_parts) > 0 and job_parts[0] == self.user:
                            if current_schedd:
                                schedds_with_user_jobs.add(current_schedd)

                if not schedds_with_user_jobs:
                    print("No condor jobs found for user.")
                    return

                for schedd in schedds_with_user_jobs:
                    print(f"Killing jobs on schedd: {schedd}")
                    kill_command = f"condor_rm -name {schedd} {self.user}"
                    print(f"Executing: {kill_command}")
                    kill_com = subprocess.Popen(kill_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                    kill_res, kill_err = kill_com.communicate()
                    if kill_res:
                        print(kill_res.decode('utf-8'))
                    if kill_err:
                        print(kill_err.decode('utf-8'))

        except Exception as e:
            print(f"Error killing condor jobs: {e}")

def main():
    config = SimulationConfig()

    parser = argparse.ArgumentParser(description="Function to create mac, shell and batch job scripts for WCSim, MDT and fiTQun")
    parser.add_argument('-p', '--pid', help='particle name (mu-, e-, etc.)')
    source_mode = parser.add_mutually_exclusive_group()
    source_mode.add_argument('-b', '--beam', help='generate beam with KE in MeV, wallDistance in cm (e.g. 100,0)')
    source_mode.add_argument('--realistic-beam', help='generate a realistic beam: KE,WallDistance,EnergySigma,BeamSigma (MeV,cm,MeV,cm)')
    source_mode.add_argument('-u', '--uniform', help='generate random vertices with uniform KE in MeV (e.g. 0,2000)')
    source_mode.add_argument('-m', '--cosmics', action='store_true', help='generate cosmic muon events')
    parser.add_argument('--save-primary-truth', action='store_true', help='save primary-particle truth')
    parser.add_argument('--save-all-truth', action='store_true', help='save all-particle truth')
    parser.add_argument('-n', '--nevs', type=int, help='number of events per file')
    parser.add_argument('-f', '--nfiles', type=int, help='number of files to be generated')
    parser.add_argument('-s', '--seed', type=int, help='RNG seed used in this script')
    parser.add_argument('-c', '--cds', action='store_true', help='disable CDS in WCSim')
    parser.add_argument('--wcsim', action='store_true', help='disable WCSim execution')
    parser.add_argument('--mdt', action='store_true', help='disable MDT execution')
    parser.add_argument('--fq', action='store_true', help='disable fiTQun execution')
    parser.add_argument('--licketyfit', metavar='PATH', help='enable LicketyFit using this checkout')
    parser.add_argument('--licketyfit-python', metavar='PATH', help='host Python executable for LicketyFit')
    parser.add_argument('--datatools', metavar='PATH', help='DataTools checkout for WCSim ROOT conversion')
    parser.add_argument('--lf-particle', choices=('muon', 'pion', 'kaon', 'proton'), help='LicketyFit particle hypothesis')
    parser.add_argument('--lf-energy-mev', type=float, help='LicketyFit sample energy label in MeV')
    parser.add_argument('--lf-seeding', choices=('general', 'beam'), default='general')
    parser.add_argument('--lf-memory-mb', type=int, default=5120)
    parser.add_argument('--lf-cpus', type=int, default=1)
    parser.add_argument('--lf-walltime', default='24:00:00', help='requested walltime HH:MM:SS')
    parser.add_argument('--lf-condor-schedd', help='HTCondor schedd name for LicketyFit jobs')
    parser.add_argument('--confirm-legacy-idle', action='store_true', help='confirm that matching legacy jobs are not active')
    parser.add_argument('--adopt-unverified', action='store_true', help='adopt existing unrecorded ROOT inputs as unverified')
    parser.add_argument('--dry-run', action='store_true', help='generate jobs without contacting a scheduler')
    parser.add_argument('-k', '--sukap', nargs='?', const='all', default=None, help='submit batch jobs on sukap. Optional: queue name (default: all)')
    parser.add_argument('-d', '--cedar', help='submit batch jobs on cedar with specified RAP account')
    parser.add_argument('-w', '--wcsimBuildDir', help='path to WCSim build directory')
    parser.add_argument('--condor', nargs='?', const='tomorrow', default=None, choices=CONDOR_FLAVOURS, help='submit batch jobs on lxplus. Optional: JobFlavour (default: tomorrow)')

    args = parser.parse_args()

    if args.pid:
        config.ParticleName = args.pid
    if args.beam:
        config.useBeam = True
        config.useUniform = False
        config.useCosmics = False
        vals = args.beam.strip().split(",")
        config.ParticleKE = float(vals[0])
        config.wallD = float(vals[1])
        config.ParticlePosz = -(config.TankRadius - config.wallD)
    if args.realistic_beam:
        vals = args.realistic_beam.split(',')
        if len(vals) != 4:
            parser.error('--realistic-beam requires KE,WallDistance,EnergySigma,BeamSigma')
        try:
            energy, wall_distance, energy_sigma, beam_sigma = (float(value) for value in vals)
        except ValueError:
            parser.error('--realistic-beam values must be numbers')
        if not all(math.isfinite(value) for value in (energy, wall_distance, energy_sigma, beam_sigma)):
            parser.error('--realistic-beam values must be finite numbers')
        if energy < 0 or energy_sigma <= 0 or beam_sigma <= 0:
            parser.error('KE must be non-negative and EnergySigma and BeamSigma must be greater than zero')
        config.useBeam = False
        config.useRealisticBeam = True
        config.useUniform = False
        config.useCosmics = False
        config.ParticleKE = energy
        config.wallD = wall_distance
        config.ParticlePosz = -(config.TankRadius - config.wallD)
        config.EnergySigma = energy_sigma
        config.BeamSigma = beam_sigma
    if args.uniform:
        config.useBeam = False
        config.useUniform = True
        config.useCosmics = False
        vals = args.uniform.strip().split(",")
        config.ParticleKELow = float(vals[0])
        config.ParticleKEHigh = float(vals[1])
    if args.cosmics:
        config.useBeam = False
        config.useRealisticBeam = False
        config.useUniform = False
        config.useCosmics = True
    config.savePrimaryParticleTruth = args.save_primary_truth
    config.saveAllParticleTruth = args.save_all_truth
    if args.nevs is not None:
        config.nevs = args.nevs
    if args.nfiles is not None:
        config.nfiles = args.nfiles
    if args.seed is not None:
        config.rngseed = args.seed
    if args.cds:
        config.useCDS = False
    if args.wcsim:
        config.runWCSim = False
    if args.mdt:
        config.runMDT = False
    if args.fq:
        config.runFQ = False
    if args.sukap is not None:
        config.submit_sukap_jobs = True
        if args.sukap != 'all':
            config.sukap_queue = args.sukap
    if args.cedar:
        config.submit_cedar_jobs = True
        config.rapaccount = args.cedar
    if args.condor is not None:
        config.submit_condor_jobs = True
        if args.condor != 'tomorrow':
            config.condor_queue = args.condor
    if args.wcsimBuildDir:
        config.wcsim_build_dir = args.wcsimBuildDir

    config.licketyfitdir = args.licketyfit
    config.licketyfit_python = args.licketyfit_python
    config.datatools = args.datatools
    config.lf_particle = args.lf_particle
    config.lf_energy_mev = args.lf_energy_mev
    config.lf_seeding = args.lf_seeding
    config.lf_memory_mb = args.lf_memory_mb
    config.lf_cpus = args.lf_cpus
    config.lf_walltime = args.lf_walltime
    config.lf_condor_schedd = args.lf_condor_schedd
    config.confirm_legacy_idle = args.confirm_legacy_idle
    config.adopt_unverified = args.adopt_unverified
    config.dry_run = args.dry_run

    try:
        config.validate()
        for stage in licketyfit_production.enabled_reconstructions(config):
            if stage.preflight_method:
                getattr(config, stage.preflight_method)()
    except ValueError as exc:
        parser.error(str(exc))

    fgen = FileGenerator(config)
    fgen.create_directories()
    fgen.generate_mac_files()
    fgen.generate_shell_scripts()

    submitter = JobSubmitter(config, fgen)
    submitter.submit_sukap()
    submitter.submit_cedar()
    submitter.submit_condor()

if __name__ == '__main__':
    main()
