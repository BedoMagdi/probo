#!/usr/bin/env python3
"""Runs a project's test suite with and without its best JVM flag combination, on a list of commits.

On each commit, the test suite is run NUM_RUNS times under the default configuration and NUM_RUNS
times under the best combination that was found for the project.
"""
import os
import argparse
import shutil
import subprocess
import time
import pandas as pd
from evaluator import MVN_BIN
from utils import get_module_time, get_all_individual_tests_runtime, write_to_json

NUM_RUNS = 5  # runs of each configuration on each commit
MVN_OPTS = "-Djacoco.skip=true -Dmaven.javadoc.skip=true -Drat.skip=true -Dmaven.test.failure.ignore=true -fn -Dlicense.skip=true -Dcheckstyle.skip -Denforcer.skip=true -Dspotbugs.skip=true -Dfindbugs.skip=true -DfailIfNoTests=false -Dmaven.failAtEnd=true".split()


def load_best_flags(best_csv_path, project_name):
    """Return the flags of the project's best combination, or None if the CSV file has none for it.

    The file has the columns Project and Flags. If it also has "Reduction %" and several rows for
    the project, the row with the largest reduction is used.
    """
    df = pd.read_csv(best_csv_path)
    if "Project" not in df.columns or "Flags" not in df.columns:
        raise ValueError(f"{best_csv_path} must have 'Project' and 'Flags' columns")

    rows = df[df["Project"] == project_name]
    if rows.empty:
        print(f"[WARN] No best combination found for project '{project_name}' in {best_csv_path}")
        return None
    if "Reduction %" in rows.columns:
        rows = rows.sort_values(by="Reduction %", ascending=False)

    flags = rows.iloc[0]["Flags"]
    if pd.isna(flags):
        print(f"[WARN] Flags for project '{project_name}' are NaN/empty.")
        return None
    return str(flags).strip()


def load_commits(commit_file):
    """Return the (commit_number, sha) pairs of a CSV file with the columns commit_number and sha."""
    df = pd.read_csv(commit_file)
    df.columns = [c.lower().strip() for c in df.columns]
    if "commit_number" not in df.columns or "sha" not in df.columns:
        raise ValueError(f"{commit_file} must have columns: commit_number, sha")

    df = df.dropna(subset=["sha"])
    return list(zip(df["commit_number"].astype(str), df["sha"].astype(str)))


def clone(url, work_dir):
    """Clone the project into work_dir unless it is already there, and return the clone's directory."""
    repo_dir = os.path.join(work_dir, "_".join(url.rstrip("/").split("/")[-2:]))  # owner_repo
    if not os.path.isdir(repo_dir):
        print(f"[INFO] Cloning {url} into {repo_dir}")
        if subprocess.run(["git", "clone", url, repo_dir]).returncode != 0:
            raise RuntimeError(f"Failed to clone {url}")
    return repo_dir


def checkout(repo_dir, sha):
    subprocess.run(["git", "fetch", "--all", "--quiet"], cwd=repo_dir)
    if subprocess.run(["git", "checkout", "-f", sha], cwd=repo_dir).returncode != 0:
        raise RuntimeError(f"Failed to checkout {sha} in {repo_dir}")

def run_combination(repo_dir, commit_dir, flags, name, insert_script):
    """Run the test suite NUM_RUNS times with the given JVM flags; an empty string stands for the default.

    Appends the output of every Maven run to <commit_dir>/commit.log, and writes the runs that left
    surefire reports to <commit_dir>/verification_runs/<name>.json.
    """
    surefire_reports_dir = os.path.join(repo_dir, "target", "surefire-reports")
    out_dir = os.path.join(commit_dir, "verification_runs")
    os.makedirs(out_dir, exist_ok=True)

    insert_cmd = ["python3", insert_script, "--dir", repo_dir]
    mvn_cmd = [MVN_BIN, "clean", "test"] + ([f"-DargLine={flags}"] if flags else []) + MVN_OPTS
    mvn_cmd_text = " ".join([MVN_BIN, "clean", "test"] + ([f'-DargLine="{flags}"'] if flags else []) + MVN_OPTS)  # for the log

    # -DargLine has no effect on a pom.xml that sets argLine itself, so the arguments are also inserted there
    if flags:
        subprocess.run(insert_cmd + [f"--args={flags}"])

    runtimes, summaries, test_times_lst = [], [], []
    for run in range(1, NUM_RUNS + 1):
        print(f"[INFO] Running tests for '{name}' (run {run}/{NUM_RUNS}): {mvn_cmd_text}")
        shutil.rmtree(surefire_reports_dir, ignore_errors=True)

        start_time = time.time()
        result = subprocess.run(mvn_cmd, cwd=repo_dir, capture_output=True, text=True)
        elapsed = time.time() - start_time

        with open(os.path.join(commit_dir, "commit.log"), "a", encoding="utf-8") as log:
            log.write("\n" + "=" * 80 + "\n")
            log.write(f"COMBINATION: {name}\n")
            log.write(f"RUN: {run}/{NUM_RUNS}\n")
            log.write(f"FLAGS: {flags}\n")
            log.write(f"CMD: {mvn_cmd_text}\n")
            log.write(f"RETURN CODE: {result.returncode}\n")
            log.write(f"ELAPSED: {elapsed:.2f}s\n")
            log.write("-" * 80 + "\n")
            if result.stdout:
                log.write("STDOUT:\n")
                log.write(result.stdout)
            if result.stderr:
                log.write("\nSTDERR:\n")
                log.write(result.stderr)
            log.write("\n")

        if os.path.isdir(surefire_reports_dir):
            runtime = get_module_time(surefire_reports_dir)
            summary, test_times = get_all_individual_tests_runtime(surefire_reports_dir)
            runtimes.append(runtime)
            summaries.append(summary)
            test_times_lst.append(test_times)
            print(f"[INFO] Measured runtime for '{name}' (run {run}): {runtime}")
        else:
            print(f"[WARN] No surefire reports found for '{name}' (run {run})")

    subprocess.run(insert_cmd + ["--restore"])

    if runtimes:
        write_to_json(runtimes, summaries, test_times_lst, os.path.join(out_dir, f"{name}.json"))
    else:
        print(f"[WARN] No successful runs for '{name}', no JSON written.")


def main():
    parser = argparse.ArgumentParser(description="Run a project's tests with and without its best JVM flag combination, on a list of commits.")
    parser.add_argument("--url", required=True, help="Git URL of the project")
    parser.add_argument("--project-name", required=True, help="name of the project in the CSV file of best combinations and in the results directory")
    parser.add_argument("--commit-file", required=True, help="CSV file with the commits to run on (columns: commit_number, sha)")
    parser.add_argument("--best-csv", required=True, help="CSV file with the best combination of each project (columns: Project, Flags)")
    parser.add_argument("--results-dir", required=True, help="directory to write the results to")
    parser.add_argument("--insert-script", required=True, help="path to insert_jvm_args_in_pom.py")
    parser.add_argument("--work-dir", default="experiment-best-flags", help="directory to clone the project into")
    args = parser.parse_args()

    project_name = args.project_name
    results_dir = os.path.abspath(args.results_dir)
    insert_script = os.path.abspath(args.insert_script)
    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    best_flags = load_best_flags(args.best_csv, project_name)
    if best_flags is None:
        print(f"[WARN] No best flags for {project_name}, skipping project.")
        return
    print(f"[INFO] Best flags for {project_name}: {best_flags}")

    commits = load_commits(args.commit_file)
    if not commits:
        print(f"[WARN] No commits in {args.commit_file}, skipping project.")
        return

    repo_dir = clone(args.url, work_dir)

    for commit_number, sha in commits:
        print(f"\n========== {project_name} – commit #{commit_number} – SHA {sha} ==========\n")
        commit_dir = os.path.join(results_dir, project_name, commit_number)
        os.makedirs(commit_dir, exist_ok=True)

        # A commit that cannot be checked out or measured is logged and skipped
        try:
            checkout(repo_dir, sha)
            run_combination(repo_dir, commit_dir, "", "default", insert_script)
            run_combination(repo_dir, commit_dir, best_flags, "c_best", insert_script)
        except Exception as e:
            print(f"[ERROR] Failure for commit {commit_number} ({sha}): {e}")
            with open(os.path.join(commit_dir, "commit.log"), "a", encoding="utf-8") as log:
                log.write("\n" + "=" * 80 + "\n")
                log.write(f"EXCEPTION during commit {commit_number} ({sha}): {e}\n")

    print(f"[INFO] Done for project {project_name}")


if __name__ == "__main__":
    main()
