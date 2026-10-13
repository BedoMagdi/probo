#!/bin/bash
#
# Runs an approach on every project of a project list, each project in its own Docker container.

IMAGE="runtimeprofiler-11:latest"
MAX_CONCURRENT=3  # containers running at the same time
MEMORY="8g"       # per container
CPUS=2            # per container

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

usage() {
    echo "Usage: $0 -p <projects.csv> [-a <approach>] [-t <seconds>] [-jc <jfc-config>]"
    echo "  -p,  --projfile     CSV file with one project per line: url,sha[,module]"
    echo "  -a,  --approach     approach to run: the name of its script without .py (default: probo)"
    echo "  -t,  --time-budget  seconds that the approach may search per project (default: the approach's fixed budget)"
    echo "  -jc, --jfc-config   JFR settings: the JDK's default or profile, custom (the custom.jfc next to this script),"
    echo "                      or the path to a .jfc file inside the container (default: default)"
    exit 1
}

projfile=""
approach="probo"
time_budget=""
jfc_config="default"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--projfile)
            projfile=$(realpath -e "$2")
            shift 2
            ;;
        -jc|--jfc-config)
            jfc_config="$2"
            shift 2
            ;;
        -a|--approach)
            approach="$2"
            shift 2
            ;;
        -t|--time-budget)
            time_budget="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "$projfile" ]]; then
    echo "You must provide the -p|--projfile option."
    usage
fi

if [[ ! -f "${SCRIPT_DIR}/${approach}.py" ]]; then
    echo "Unknown approach: ${approach} (${SCRIPT_DIR}/${approach}.py does not exist)"
    exit 1
fi

date

# Build the Docker image if it does not exist
if ! docker image inspect "$IMAGE" > /dev/null 2>&1; then
    docker build -t "$IMAGE" -f "${SCRIPT_DIR}/Dockerfile" "$SCRIPT_DIR" || exit 1
fi

# Run one project in a container, with this directory mounted as /Scratch
process_project() {
    local url="$1" sha="$2" module="$3"
    local name
    name=$(echo "$url" | rev | cut -d'/' -f1-2 | rev | tr '/' '_')  # owner_repo
    if [[ -n "$module" ]]; then
        name="${name}-${module//\//_}"
    fi

    echo "About to run: $url, $sha, $module"
    docker run -t --rm --name "${name}_runtimeprofiler_container" -v "${SCRIPT_DIR}:/Scratch" -m="$MEMORY" --cpus="$CPUS" "$IMAGE" \
        /bin/bash -x /Scratch/run_experiment.sh "$url" "$sha" "$module" "$jfc_config" "$approach" ${time_budget:+"$time_budget"}
}

while IFS=, read -r url sha module || [[ -n "$url" ]]; do
    if [[ -z "$url" ]]; then
        continue
    fi

    # Wait until fewer than MAX_CONCURRENT containers are running
    while [[ $(docker ps -q | wc -l) -ge $MAX_CONCURRENT ]]; do
        sleep 5
    done

    process_project "$url" "$sha" "$module" &
    sleep 5  # Give Docker time to register the new container
done < "$projfile"

wait
echo "All containers finished"
date
