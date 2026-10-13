#!/bin/bash
#
# Clones and builds one project, then runs an approach on it.
# Runs inside the Docker container as the unprivileged user, from that user's home directory.

url="$1"
sha="$2"
module="$3"
jfc_config="$4"
approach="$5"
time_budget="$6"  # optional

MVN="/home/runtimeprofiler/apache-maven/bin/mvn"
MVN_OPTS=(-Djacoco.skip=true -Dmaven.javadoc.skip=true -Drat.skip=true -Dmaven.test.failure.ignore=true
          -fn -Dlicense.skip=true -Dcheckstyle.skip -Denforcer.skip=true -Dspotbugs.skip=true -Dfindbugs.skip=true
          -DfailIfNoTests=false -Dmaven.failAtEnd=true)

echo "START run_maven.sh"
date

work_dir="$(pwd)/experiment"
res_dir="$(pwd)/results-java"
mkdir -p "$work_dir" "$res_dir"

# Each container builds the JFR parser, so it works on its own copy
cp -r /Scratch/jfr_parser "$work_dir"

cd "$work_dir" || exit 1

slug=$(echo "$url" | rev | cut -d'/' -f1-2 | rev)  # owner/repo
modified_slug="${slug//\//_}"
project_res_dir="${res_dir}/$(basename "$url")"

if [[ ! -d "$slug" ]]; then
    git clone "$url" "$slug" || exit 1
fi
cd "$slug" || exit 1
git checkout -f "$sha" || exit 1

# Build the whole project, so that a module finds the other modules it depends on
"$MVN" clean install "${MVN_OPTS[@]}" -q -DskipTests

# For a multi-module project, the approach runs the tests of the given module
if [[ -n "$module" ]]; then
    cd "$module" || exit 1
    modified_module="${module//\//_}"
    modified_slug="${modified_slug}_${modified_module}"
    project_res_dir="${project_res_dir}/${modified_module}"
fi
mkdir -p "$project_res_dir"

case "$jfc_config" in
    default|profile) jfc_file="${jfc_config}.jfc" ;;       # the JDK's own settings
    custom)          jfc_file="/Scratch/custom.jfc" ;;     # the settings of the paper's experiments
    *)               jfc_file="$jfc_config" ;;
esac

python3 "/Scratch/${approach}.py" \
    --project-dir "$(pwd)" \
    --results-dir "$project_res_dir" \
    --slug "$modified_slug" \
    --jfc-file "$jfc_file" \
    --insert-script /Scratch/insert_jvm_args_in_pom.py \
    --jfr-parser-dir "${work_dir}/jfr_parser" \
    ${time_budget:+--time-budget "$time_budget"}

date
