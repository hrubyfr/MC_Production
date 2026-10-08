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

    def get_config_string(self):
        wCDSstring = "_wCDS" if self.useCDS else ""
        
        if self.useRealisticBeam:
            beamstring = "RealisticBeam_%gMeV_%gcm_Esigma%gMeV_Bsigma%gcm_" % (
                self.ParticleKE, self.wallD, self.EnergySigma, self.BeamSigma
            )
        else:
            beamstring = "Beam_%.0fMeV_%icm_" % (self.ParticleKE, int(self.wallD)) if self.useBeam else ""
        
        uniformstring = "Uniform_%.0f_%.0fMeV_" % (self.ParticleKELow, self.ParticleKEHigh) if self.useUniform else ""
        
        cosmicsstring = "Comsics_" if self.useCosmics else ""

        configString = "%s_%s_%s%s" % (wCDSstring, self.ParticleName, beamstring, uniformstring)
        if self.useCosmics:
            configString = "%s_%s" % (wCDSstring, cosmicsstring)

        if self.savePrimaryParticleTruth:
            configString += "_PrimaryTruth"
        if self.saveAllParticleTruth:
            configString += "_AllParticleTruth"
        
        return configString

class FileGenerator:
    def __init__(self, config):
        self.cfg = config
        self.macdir = "mac"
        self.outdir = "out"
        self.logdir = "log"
        self.shelldir = "shell"
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

    def create_directories(self):
        dirs = [self.macdir, self.outdir, self.logdir, self.shelldir, self.figdir]
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

        random.seed(self.cfg.rngseed)

        for i in range(self.cfg.nfiles):
            macFile = "%s/wcsim%s%04i.mac" % (self.macdir, configString, i)
            macseed = random.randrange(int(1e9))
            
            with open(macFile, 'w') as fo:
                fo.write(macTemplate.substitute(
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
            with open(tuningFile, 'w') as fo:
                fo.write(tuningTemplate.substitute(wcsimdir=self.cfg.wcsimdir))

    def generate_shell_scripts(self):
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
            
            with open(shFile, 'w') as fo:
                fo.write(shTemplate.substitute(
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

class JobSubmitter:
    def __init__(self, config, file_generator):
        self.cfg = config
        self.fgen = file_generator

    def is_job_missing(self, i):
        configString = self.cfg.get_config_string()
        if self.cfg.runWCSim and not os.path.exists("%s/wcsim%s%04i.root" % (self.fgen.outdir, configString, i)):
            return True
        if self.cfg.runMDT and not os.path.exists("%s/mdt%s%04i.root" % (self.fgen.outdir, configString, i)):
            return True
        if self.cfg.runFQ and not os.path.exists("%s/fq%s%04i.root" % (self.fgen.outdir, configString, i)):
            return True
        return False

    def scan_jobs(self):
        n_to_submit = 0
        n_skipped = 0
        for i in range(self.cfg.nfiles):
            if self.is_job_missing(i):
                n_to_submit += 1
            else:
                n_skipped += 1
        return n_to_submit, n_skipped

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
                while True:
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
                
                with open(pjFile, 'w') as fo:
                    fo.write(shTemplate.substitute(
                        curdir=self.cfg.curdir,
                        shFile=shFile,
                        pjout=pjout,
                        pjerr=pjerr,
                        rscgrp=self.cfg.sukap_queue
                    ))

                com = subprocess.Popen("pjsub %s" % (pjFile), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                res, err = com.communicate()
                if len(err) > 0:
                    raise RuntimeError("Sukap submission failed: %s" % err.decode('utf-8'))
                else:
                    print (res.decode('utf-8'))
            else:
                n_skipped += 1
        print ("Submitted %d jobs. Skipped %d jobs due to existing files." % (n_submitted, n_skipped))

    def submit_cedar(self):
        if not self.cfg.submit_cedar_jobs: return
        
        configString = self.cfg.get_config_string()
        print ("Creating slurm scripts for WCSim")
        
        with open("template/slurm.sh", 'r') as f:
            slTemplate = string.Template(f.read())
            
        siffile = self.cfg.siffile

        for i in range(self.cfg.nfiles):
            slFile = "%s/slurm%s%04i.sh" % (self.fgen.sldir, configString, i)
            slout = "%s/slurm%s%04i" % (self.fgen.sloutdir, configString, i)
            slerr = "%s/slurm%s%04i" % (self.fgen.slerrdir, configString, i)
            
            with open(slFile, 'w') as fo:
                fo.write(slTemplate.substitute(
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
                com = subprocess.Popen("sbatch %s" % (slFile), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                res, err = com.communicate()
                if len(err) > 0:
                    raise RuntimeError("Cedar submission failed: %s" % err.decode('utf-8'))
                else:
                    print (res.decode('utf-8'))
            else:
                n_skipped += 1
        print ("Submitted %d jobs. Skipped %d jobs due to existing files." % (n_submitted, n_skipped))

    def submit_condor(self):
        if not self.cfg.submit_condor_jobs: return

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
            
            with open(condorFile, 'w') as fo:
                fo.write(condorTemplate.substitute(shfile=shfile, out=out, err=err, log=log, JobFlavour=self.cfg.condor_queue))

        print ("Submitting condor jobs on lxplus")
        n_submitted = 0
        n_skipped = 0
        for i in range(self.cfg.nfiles):
            if self.is_job_missing(i):
                n_submitted += 1
                condorFile = "%s/condor%s%04i.sub" % (self.fgen.condordir, configString, i)
                com = subprocess.Popen("module load lxbatch/eossubmit && condor_submit %s" % (condorFile), shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
                res, err = com.communicate()
                if len(err) > 0:
                    raise RuntimeError("Condor submission failed: %s" % err.decode('utf-8'))
                else:
                    print (res.decode('utf-8'))
            else:
                n_skipped += 1
        print ("Submitted %d jobs. Skipped %d jobs due to existing files." % (n_submitted, n_skipped))

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

    config.validate()

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
