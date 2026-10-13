#!/bin/bash
#
# Runs the experiment for one project inside its Docker container (as root),
# and copies the results to the directory mounted from the host.

url="$1"
sha="$2"
module="$3"
jfc_config="$4"
approach="$5"
time_budget="$6"  # optional

USERNAME="runtimeprofiler"
SRC_DIR="/home/${USERNAME}/results-java"  # where run_maven.sh writes the results
DST_DIR="/Scratch/results-java"           # mounted from the host

# The results are stored per approach and project, and per module for a multi-module project
project_name=$(basename "$url")
slug=$(echo "$url" | rev | cut -d'/' -f1-2 | rev)
log_name="${slug//\//_}"
results_subdir="$project_name"
if [[ -n "$module" ]]; then
    modified_module="${module//\//_}"
    log_name="${log_name}_${modified_module}"
    results_subdir="${results_subdir}/${modified_module}"
fi
logfile="${log_name}.log"

su "$USERNAME" -c "/Scratch/run_maven.sh ${url} ${sha} \"${module}\" ${jfc_config} ${approach} ${time_budget}" 2>&1 | tee "$logfile"

# Do not overwrite the results of an earlier run
dst="${DST_DIR}/${approach}/${results_subdir}"
if [[ -d "$dst" ]]; then
    dst="${dst}_$(date +"%Y%m%d_%H%M%S")"
    echo "Destination exists, using new path: $dst"
fi
mkdir -p "$dst"

src="${SRC_DIR}/${results_subdir}"
if [[ -d "$src" ]]; then
    cp -r "$src/"* "$dst/"
fi
cp "$logfile" "$dst/"

# Make the results accessible to the host user
chmod -R 777 "$DST_DIR"
