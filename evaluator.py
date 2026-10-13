"""Evaluation of JVM flag combinations on a Maven project, shared by the three approaches."""
import json
import os
import shutil
import subprocess
import time
from utils import *

NUM_RUNS = 5 # unprofiled runs that measure the runtime of a combination
TIMEOUT_LIMIT_MULTIPLIER = 2 # a combination times out after this multiple of the default runtime

MVN_BIN = "/home/runtimeprofiler/apache-maven/bin/mvn"  # path inside the Docker image
MVN_OPTS="-Djacoco.skip=true -Dmaven.javadoc.skip=true -Drat.skip=true -Dmaven.test.failure.ignore=true -fn -Dlicense.skip=true -Dcheckstyle.skip -Denforcer.skip=true -Dspotbugs.skip=true -Dfindbugs.skip=true -DfailIfNoTests=false -DdetailLevel=elaborate -DskipSurefireTests=true -Dmaven.failAtEnd=true -DskipUTs=true".split()


class Evaluator:
    """Runs a project's test suite under JVM flag combinations and records the results.

    This class evaluates a combination the way the two baselines do, with NUM_RUNS unprofiled runs.
    probo.py extends it with a profiled run.
    """

    def __init__(self, project_root_dir, res_dir, insert_jvm_args_in_pom_path, logger, deadline=None):
        self.project_root_dir = project_root_dir
        self.res_dir = res_dir
        self.insert_jvm_args_in_pom_path = insert_jvm_args_in_pom_path
        self.logger = logger
        self.deadline = deadline          # time.time() value after which no further combination is started
        self.surefire_reports_dir = os.path.join(project_root_dir, "target", "surefire-reports")

        self.runtimes = {}                # combination -> runtimes of its unprofiled runs
        self.comb_to_dir = {}             # combination -> short name (c1, c2, ...) used for its files
        self.dir_to_comb = {}             # reverse mapping
        self.failing_combinations = []    # combinations that changed the outcome of a test
        self.default_test_results = None  # test -> outcome under the default configuration
        self.default_mvn_runtime = None   # duration of the default configuration's first Maven run

    def out_of_time(self):
        return self.deadline is not None and time.time() >= self.deadline

    def run_combs(self, combs):
        """Evaluate flag combinations; an empty string stands for the default configuration.

        Stops before a combination if the deadline has passed.
        Returns the combinations whose runtime was measured.
        """
        successful = []
        for comb in combs:
            if self.out_of_time():
                self.logger.info("Overall time limit reached. Stopping execution.")
                break

            comb = comb or "default"
            if comb != "default" and comb not in self.comb_to_dir:
                dir_name = f"c{len(self.comb_to_dir) + 1}"
                self.comb_to_dir[comb] = dir_name
                self.dir_to_comb[dir_name] = comb
            dir_name = self.comb_to_dir.get(comb, "default")
            self.logger.info(f"Processing combination: {comb}")

            # A combination may take at most TIMEOUT_LIMIT_MULTIPLIER times as long as the default
            timeout = None
            if comb != "default" and self.default_mvn_runtime is not None:
                timeout = int(self.default_mvn_runtime * TIMEOUT_LIMIT_MULTIPLIER) or None

            if self._evaluate(comb, dir_name, timeout):
                successful.append(comb)

        return successful

    def save_results(self):
        """Write the table of all measured combinations, the short names, and the failing combinations."""
        with open(os.path.join(self.res_dir, "combination_mapping.json"), 'w') as f:
            json.dump(self.comb_to_dir, f, indent=2)

        sorted_combinations_results = get_sorted_combinations_results(self.runtimes, list(self.runtimes.keys()))
        create_table_all(self.res_dir, sorted_combinations_results)

        if self.failing_combinations:
            with open(os.path.join(self.res_dir, "failing_combinations.txt"), 'w') as f:
                for comb in self.failing_combinations:
                    f.write("%s\n" % comb)

    def _evaluate(self, comb, dir_name, timeout):
        """Measure the runtime of a combination over NUM_RUNS runs.

        The combination is discarded if a run changes the outcome of a test, or if a run leaves no
        surefire reports. If a run times out, the combination is recorded with the timeout as the
        runtime of that run and of the remaining ones.
        Returns True if the runtime was measured without a timeout.
        """
        comb_dir = os.path.join(self.res_dir, dir_name)  # keeps the surefire reports of every run
        out_dir = os.path.join(self.res_dir, "comb_runs")
        os.makedirs(out_dir, exist_ok=True)

        timed_out = False
        runtimes, summaries, test_times_lst = [], [], []
        for run in range(1, NUM_RUNS + 1):
            run_dir = os.path.join(comb_dir, str(run))
            os.makedirs(run_dir, exist_ok=True)
            try:
                mvn_time, report = self._run_tests("" if comb == "default" else comb, timeout)
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Combination {comb} run {run} timed out after {timeout} seconds")
                runtimes += [timeout] * (NUM_RUNS - run + 1)
                timed_out = True
                break

            if comb == "default" and self.default_mvn_runtime is None and mvn_time is not None:
                self.default_mvn_runtime = mvn_time
                self.logger.info(f"Default runtime detected: {mvn_time} seconds")

            if report is None:
                self.logger.warning(f"Combination {comb} run {run} failed - no surefire reports found")
            else:
                runtime, summary, test_times = report
                test_results = {test: info['result'] for test, info in test_times.items()}
                if self.default_test_results and test_results != self.default_test_results:
                    self.failing_combinations.append(comb)
                    self.logger.warning(f"Combination '{comb}' failed with different test results")
                    shutil.rmtree(comb_dir)
                    return False
                runtimes.append(runtime)
                summaries.append(summary)
                test_times_lst.append(test_times)
                self.logger.info(f"Combination {comb} completed with runtime: {runtime}")

            if os.path.exists(self.surefire_reports_dir):
                shutil.copytree(self.surefire_reports_dir, os.path.join(run_dir, "surefire-reports"), dirs_exist_ok=True)

        if timed_out:
            shutil.rmtree(comb_dir)

        if comb == "default" and self.default_test_results is None:
            self.default_test_results = {test: info['result'] for test, info in test_times_lst[0].items()}

        if len(runtimes) < NUM_RUNS:
            return False
        self.runtimes[comb] = runtimes
        if timed_out:
            return False
        write_to_json(runtimes, summaries, test_times_lst, os.path.join(out_dir, f"{dir_name}.json"))
        return True

    def _run_tests(self, arg_line, timeout):
        """Run the test suite once with the given JVM arguments.

        Returns (mvn_time, report). mvn_time is the duration of the Maven run, or None if Maven
        exited with an error. report is (runtime, summary, test_times) as read from the surefire
        reports, or None if there are no usable reports.
        Raises subprocess.TimeoutExpired if the run exceeds the timeout.
        """
        insert_cmd = ["python3", self.insert_jvm_args_in_pom_path, "--dir", self.project_root_dir]
        mvn_cmd = [MVN_BIN, "clean", "test", f"-DargLine={arg_line}"] + MVN_OPTS
        try:
            # -DargLine has no effect on a pom.xml that sets argLine itself, so the arguments are also inserted there
            if arg_line:
                subprocess.run(insert_cmd + [f"--args={arg_line}"])
            start_time = time.time()
            result = subprocess.run(mvn_cmd, cwd=self.project_root_dir, capture_output=True, text=True, timeout=timeout)
            mvn_time = time.time() - start_time
        finally:
            subprocess.run(insert_cmd + ["--restore"])

        print(result.stdout, flush=True)
        if result.returncode != 0:
            print(result.stderr, flush=True)

        report = None
        if os.path.exists(self.surefire_reports_dir):
            try:
                runtime = get_module_time(self.surefire_reports_dir)
                summary, test_times = get_all_individual_tests_runtime(self.surefire_reports_dir)
                report = (runtime, summary, test_times)
            except Exception as e:
                self.logger.error(f"Error reading the surefire reports: {e}")

        return (mvn_time if result.returncode == 0 else None), report
