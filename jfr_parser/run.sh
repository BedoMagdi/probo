#!/bin/bash
#
# Extracts the runtime metrics from the JFR recordings under a directory (see README.md).
# Run it from this directory, with absolute paths. It needs Java 11 or later.

MVN="${MVN:-/home/runtimeprofiler/apache-maven/bin/mvn}"  # the Maven of the Docker image

src=""
dest="parser-results"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--dest)
            dest="$2"
            shift 2
            ;;
        *)
            if [ "$src" = "" ]; then
                src="$1"
                shift 1
            else
                echo "Unknown option: $1"
                exit 1
            fi
            ;;
    esac
done

if [ "$src" = "" ] || [ ! -d "$src" ]; then
    echo "Usage: ./run.sh <profiling_results_path> [-d <destination_dir_path>]"
    exit 1
fi

mkdir -p "$dest"

# One by_test_suite.csv per recording. The parser runs inside Maven's JVM and holds the recordings that it
# reads in parallel in memory.
(cd parser && MAVEN_OPTS="-Xmx4g" "$MVN" compile exec:java -Dexec.args="$src $dest")
parser_status=$?

# One avg_by_test_suite.csv per group of recordings, also for those parsed before a failure
python3 postprocess/avg.py "$dest" || exit
exit $parser_status
