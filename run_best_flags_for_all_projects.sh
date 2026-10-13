#!/bin/bash
#
# Runs every project of a project list with and without its best flag combination on a list of
# commits, each project in its own Docker container.

IMAGE="runtimeprofiler-11:latest"
MAX_CONCURRENT=3            # containers running at the same time
MEMORY="8g"                 # per container
CPUS=2                      # per container
USERNAME="runtimeprofiler"  # the image's unprivileged user

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

usage() {
    echo "Usage: $0 -p <projects.csv> -c <commits-dir> -b <best-combinations.csv> [-r <results-dir>]"
    echo "  -p, --projfile     CSV file with one project per line: url,sha[,module]; only the URL is used"
    echo "  -c, --commits-dir  directory with a <repo>.csv file per project: the commits to run on (columns: commit_number, sha)"
    echo "  -b, --best-csv     CSV file with the best combination of each project (columns: Project, Flags)"
    echo "  -r, --results-dir  directory to write the results to (default: results-best-flags)"
    exit 1
}

projfile=""
commits_dir=""
best_csv=""
results_dir="results-best-flags"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--projfile)
            projfile=$(realpath -e "$2")
            shift 2
            ;;
        -c|--commits-dir)
            commits_dir=$(realpath -e "$2")
            shift 2
            ;;
        -b|--best-csv)
            best_csv=$(realpath -e "$2")
            shift 2
            ;;
        -r|--results-dir)
            results_dir="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "$projfile" || -z "$commits_dir" || -z "$best_csv" ]]; then
    echo "You must provide the -p|--projfile, -c|--commits-dir and -b|--best-csv options."
    usage
fi

date

mkdir -p "$results_dir"
results_dir=$(realpath "$results_dir")

# Build the Docker image if it does not exist
if ! docker image inspect "$IMAGE" > /dev/null 2>&1; then
    docker build -t "$IMAGE" -f "${SCRIPT_DIR}/Dockerfile" "$SCRIPT_DIR" || exit 1
fi

# Run one project in a container, with this directory mounted as /Scratch.
# The results directory is made writable for the container's user, and its content readable for the host user.
process_project() {
    local url="$1" module="$2"
    local project name
    project=$(basename "$url")
    name=$(echo "$url" | rev | cut -d'/' -f1-2 | rev | tr '/' '_')  # owner_repo
    if [[ -n "$module" ]]; then
        name="${name}-${module//\//_}"
    fi

    echo "About to run (best flags): $url, $project"
    docker run -t --rm --name "${name}_bestflags_container" \
        -v "${SCRIPT_DIR}:/Scratch" -v "${commits_dir}:/commits" -v "$(dirname "$best_csv"):/best" -v "${results_dir}:/results" \
        -m="$MEMORY" --cpus="$CPUS" "$IMAGE" \
        /bin/bash -lc "
            mkdir -p /results && chmod -R 777 /results && \
            cd /Scratch && \
            su ${USERNAME} -c \"python3 run_best_flags_for_project.py \
                --url '$url' \
                --project-name '$project' \
                --commit-file '/commits/${project}.csv' \
                --best-csv '/best/$(basename "$best_csv")' \
                --results-dir '/results' \
                --insert-script '/Scratch/insert_jvm_args_in_pom.py' \
                --work-dir '/home/${USERNAME}/experiment-best-flags'\"
            chmod -R 777 /results
        "
}

while IFS=, read -r url sha module || [[ -n "$url" ]]; do
    if [[ -z "$url" ]]; then
        continue
    fi

    # Wait until fewer than MAX_CONCURRENT containers are running
    while [[ $(docker ps -q | wc -l) -ge $MAX_CONCURRENT ]]; do
        sleep 5
    done

    process_project "$url" "$module" &
    sleep 5  # Give Docker time to register the new container
done < "$projfile"

wait
echo "All containers finished"
date

