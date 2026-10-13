"""Random search baseline: evaluates valid JVM flag combinations that are sampled at random."""
import os
import argparse
import numpy as np
import random
import logging
import time
from utils import *
from evaluator import Evaluator

NUM_COMBINATIONS = 100  # combinations to evaluate, next to the default configuration
RANDOM_SEED = 10


def execute(project_path, res_dir, insert_jvm_args_in_pom_path, time_budget=None):
    start_time = time.time()

    # Set a fixed random seed for reproducibility
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    logger = logging.getLogger("JVMBayesianOptimizer")
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(os.path.join(res_dir, "JVMBayesianOptimizer.log"))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    # Leave out the flags that the project already sets in its pom.xml
    flag_space = FLAGS[:]
    if check_argLine_options(project_path, ['-Xms', '-Xmx', 'MaxHeapSize', 'InitialHeapSize']):
        flag_space = [flag for flag in flag_space if flag not in ('-Xmx15920m', '-Xmx3980m', '-Xms996m', '-Xms249m')]
    if check_argLine_options(project_path, ['-Xss', 'ThreadStackSize']):
        flag_space = [flag for flag in flag_space if flag not in ('-Xss512k', '-Xss2048k')]

    deadline = None
    if time_budget is not None:
        deadline = start_time + time_budget
        logger.info(f"Overall time limit = {time_budget}s")
    evaluator = Evaluator(project_path, res_dir, insert_jvm_args_in_pom_path, logger, deadline)

    seen = set()  # combinations that were already tried
    combs = get_random_combinations(flag_space, NUM_COMBINATIONS, len(flag_space), seen)
    seen.update(combs)
    logger.info(f"Generated random combinations: {combs}")
    evaluator.run_combs([''] + combs)

    # Sample further combinations until the default and NUM_COMBINATIONS combinations have a runtime
    while len(evaluator.runtimes) < NUM_COMBINATIONS + 1 and not evaluator.out_of_time():
        combs = get_random_combinations(flag_space, NUM_COMBINATIONS + 1 - len(evaluator.runtimes), len(flag_space), seen)
        seen.update(combs)
        logger.info(f"Generated {len(combs)} more random combinations: {combs}")
        evaluator.run_combs(combs)

    minutes, seconds = divmod(time.time() - start_time, 60)
    hours, minutes = divmod(minutes, 60)
    logger.info(f"Random search total time: {int(hours)} hours, {int(minutes)} minutes, and {seconds:.2f} seconds")
    logger.info(f"Completed {len(evaluator.runtimes)} total combinations")

    evaluator.save_results()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Evaluate random JVM flag combinations on the test suite of a Maven project.")
    parser.add_argument("--project-dir", required=True, help="directory of the Maven project (or module) whose tests are run")
    parser.add_argument("--results-dir", required=True, help="directory to write the results to")
    parser.add_argument("--insert-script", required=True, help="path to insert_jvm_args_in_pom.py")
    parser.add_argument("--time-budget", type=float, help="stop starting new combinations after this many seconds (default: no limit)")
    # run_maven.sh calls every approach with the same options; random search does not profile, so it ignores these
    for unused in ("--slug", "--jfc-file", "--jfr-parser-dir"):
        parser.add_argument(unused, help=argparse.SUPPRESS)
    args = parser.parse_args()

    execute(args.project_dir, args.results_dir, args.insert_script, args.time_budget)
