"""BOCA baseline: Bayesian optimization with a random forest that predicts the objective directly from the flags.

Adapted from BOCA (Chen et al., "Efficient Compiler Autotuning via Bayesian Optimization", ICSE 2021).
A configuration is a list with one 0/1 entry per flag of the flag space.
"""
import os
import argparse
import time
import random
import numpy as np
import logging
from sklearn.ensemble import RandomForestRegressor
from scipy.stats import norm
import math
from utils import *
from evaluator import Evaluator

NUM_CONFIGURATIONS = 20  # configurations to evaluate when there is no time budget, including the initial ones
INITIAL_SAMPLE_SIZE = 2  # random configurations evaluated before the model is used
RANDOM_SEED = 10

# BOCA's hyperparameters
FNUM = 8        # number of most important flags whose settings are enumerated
RNUM = 2 ** 8   # neighbors generated per setting of those flags, before the decay starts
OFFSET = 20     # number of evaluated configurations after which the decay starts
DECAY = 0.5
SCALE = 10

FAILED = -9999  # score of a configuration that could not be measured


def get_objective_score(conf, flag_space, evaluator, logger):
    """Evaluate a configuration, and the default configuration if it has no runtimes yet.

    Returns minus the median speedup over the default configuration (lower is better), or FAILED.
    """
    flags = [flag for flag, selected in zip(flag_space, conf) if selected == 1]
    comb_str = ' '.join(validate_combination(flags))

    if 'default' not in evaluator.runtimes:
        logger.info("Running default configuration for baseline")
        evaluator.run_combs([''])
        if 'default' not in evaluator.runtimes:
            logger.error("Failed to get default configuration results")
            return FAILED

    if comb_str not in evaluator.runtimes:
        logger.info(f"Running new combination: {comb_str}")
        evaluator.run_combs([comb_str])
        if comb_str not in evaluator.runtimes:
            logger.warning(f"Failed to get results for combination: {comb_str}")
            return FAILED

    speedups = [default_time / comb_time
                for comb_time, default_time in zip(evaluator.runtimes[comb_str], evaluator.runtimes['default'])]
    return -np.median(speedups)


def get_ei(pred, eta):
    """Expected improvement over eta, from the predictions of the forest's trees (one row per tree)."""
    pred = np.array(pred).transpose(1, 0)
    m = np.mean(pred, axis=1)
    s = np.std(pred, axis=1)

    def calculate_f():
        z = (eta - m) / s
        return (eta - m) * norm.cdf(z) + s * norm.pdf(z)

    if np.any(s == 0.0):
        s_copy = np.copy(s)
        s[s_copy == 0.0] = 1.0
        f = calculate_f()
        f[s_copy == 0.0] = 0.0
    else:
        f = calculate_f()

    return f


def search_neighbors(model, eta, rnum):
    """Generate candidate configurations and compute their expected improvement.

    Every setting of the FNUM most important flags is combined with 1 + rnum random settings of
    the other flags. Returns (configuration, expected improvement) pairs.
    """
    importances = model.feature_importances_
    num_flags = len(importances)
    flag_ids = list(range(num_flags))
    top_flags = [i for i, _ in sorted(enumerate(importances), key=lambda x: x[1], reverse=True)[:FNUM]]

    neighbors = []
    for setting in range(2 ** FNUM):
        bits = bin(setting)[2:].zfill(FNUM)
        for _ in range(1 + int(rnum)):
            neighbor = [0] * num_flags
            for flag_id in random.sample(flag_ids, random.randint(0, num_flags)):
                neighbor[flag_id] = 1
            for flag_id, bit in zip(top_flags, bits):
                neighbor[flag_id] = int(bit)
            neighbors.append(neighbor)

    pred = [tree.predict(np.array(neighbors)) for tree in model.estimators_]
    return list(zip(neighbors, get_ei(pred, eta)))


def next_configuration(train_indep, train_dep, eta, rnum):
    """Fit the model on the evaluated configurations and return the new, non-empty candidate
    with the highest expected improvement."""
    model = RandomForestRegressor()
    model.fit(np.array(train_indep), np.array(train_dep))

    candidates = sorted(search_neighbors(model, eta, rnum), key=lambda x: x[1], reverse=True)
    for conf, _ in candidates:
        if conf not in train_indep and sum(conf) > 0:
            return conf


def format_elapsed_time(seconds):
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {seconds:.2f}s"


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

    train_indep, train_dep = [], []  # the evaluated configurations and their scores

    # Evaluate random configurations
    while len(train_indep) < INITIAL_SAMPLE_SIZE and not evaluator.out_of_time():
        x = random.randint(0, 2 ** len(flag_space))
        conf = [int(bit) for bit in bin(x)[2:].zfill(len(flag_space))]
        if conf not in train_indep:
            logger.info(f"Evaluating initial configuration {len(train_indep) + 1}/{INITIAL_SAMPLE_SIZE}")
            score = get_objective_score(conf, flag_space, evaluator, logger)
            if score != FAILED:
                train_indep.append(conf)
                train_dep.append(score)
                logger.info(f"Initial configuration score: {score}")
    logger.info(f"Initial sampling phase completed in {format_elapsed_time(time.time() - start_time)}")

    best = min(train_dep, default=1e8)
    logger.info(f"Best initial result: {best}")

    # Without a time budget the search ends after NUM_CONFIGURATIONS configurations
    sigma = -SCALE ** 2 / (2 * math.log(DECAY))
    while (time_budget is not None or len(train_indep) < NUM_CONFIGURATIONS) and not evaluator.out_of_time():
        step_start_time = time.time()
        step = len(train_indep) - INITIAL_SAMPLE_SIZE + 1
        logger.info(f"Bayesian optimization step {step}")

        # Propose configurations until one of them can be measured
        rnum = RNUM * math.exp(-max(0, len(train_indep) - OFFSET) ** 2 / (2 * sigma ** 2))
        score = FAILED
        while score == FAILED and not evaluator.out_of_time():
            conf = next_configuration(train_indep, train_dep, best, rnum)
            logger.info(f"Next candidate solution: {[flag for flag, selected in zip(flag_space, conf) if selected == 1]}")
            score = get_objective_score(conf, flag_space, evaluator, logger)
            if score == FAILED:
                logger.warning("Failed to evaluate candidate, generating new configuration")
        if score == FAILED:
            break

        train_indep.append(conf)
        train_dep.append(score)
        logger.info(f"Step {step} result: {score}")
        if score < best:
            best = score
            logger.info(f"New best result found: {best}")

        logger.info(f"Step {step} completed in {format_elapsed_time(time.time() - step_start_time)}")
        logger.info(f"Elapsed time since start: {format_elapsed_time(time.time() - start_time)}")

    logger.info(f"Final best result: {best}")
    logger.info(f"Completed {len(train_indep)} total configurations")
    logger.info(f"Total execution time: {format_elapsed_time(time.time() - start_time)}")

    evaluator.save_results()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Search for JVM flag combinations on the test suite of a Maven project with BOCA.")
    parser.add_argument("--project-dir", required=True, help="directory of the Maven project (or module) whose tests are run")
    parser.add_argument("--results-dir", required=True, help="directory to write the results to")
    parser.add_argument("--insert-script", required=True, help="path to insert_jvm_args_in_pom.py")
    parser.add_argument("--time-budget", type=float, help="search for this many seconds instead of for a fixed number of configurations")
    # run_maven.sh calls every approach with the same options; BOCA does not profile, so it ignores these
    for unused in ("--slug", "--jfc-file", "--jfr-parser-dir"):
        parser.add_argument(unused, help=argparse.SUPPRESS)
    args = parser.parse_args()

    execute(args.project_dir, args.results_dir, args.insert_script, args.time_budget)
