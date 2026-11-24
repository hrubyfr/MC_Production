#!/bin/bash
#SBATCH --account=$account
#SBATCH --time=$walltime
#SBATCH --mem=${memory}M
#SBATCH --cpus-per-task=$cpus
#SBATCH --output=$output.%j.out
#SBATCH --error=$error.%j.err

set -euo pipefail
module load apptainer
cd $production
exec bash $worker