# PROBO: Profiling-Guided Bayesian Optimization of JVM Configurations

This repository contains the code for the ISSTA 2026 paper
[*Profiling-Guided Bayesian Optimization of JVM Configurations*](https://doi.org/10.1145/3832253).

PROBO searches for combinations of JVM flags that reduce the test-suite runtime of Maven-based Java projects.
It profiles test runs with Java Flight Recorder (JFR) and uses the runtime metrics from the recordings to guide a Bayesian optimization over the flag space.

The repository also contains the two baselines that the paper compares against: BOCA and random search.

## Approaches

| Approach | Script | Search | Default budget |
| --- | --- | --- | --- |
| PROBO | `probo.py` | The metrics models (one random forest per runtime metric) predict the metrics from the flags, and the performance model (a random forest) predicts the test-suite runtime from those metrics. | 20 combinations: 5 random ones, then 5 iterations of 3 |
| BOCA | `boca.py` | A random forest predicts the speedup directly from the flags. | 20 combinations |
| Random search | `random_search.py` | Samples valid flag combinations at random. | 100 combinations |

All three evaluate the default configuration first and share the search space of 72 JVM flag settings in `utils.py`, which also lists the conflicts and dependencies between flags.

A combination's runtime is measured over 5 runs of the test suite.
A run is stopped if it takes more than twice as long as the default configuration, and a combination that changes the outcome of a test is discarded.

## Requirements

- A Linux host with Docker and Bash.
- Maven-based Java projects that build and pass their tests with Java 11.
- Resources for the containers: each one is limited to 2 CPUs and 8 GB of memory, and up to 3 run at the same time. These limits are constants at the top of `create_and_run_dockers.sh`.

Everything else is installed in the Docker image built from `Dockerfile`: Ubuntu 22.04, OpenJDK 11, Maven 3.8.3, and the Python packages.

## Running

1. Create a project list, one project per line, with no header.

   ```
   https://github.com/jhy/jsoup,<commit-sha>
   https://github.com/<owner>/<repo>,<commit-sha>
   ```

   `projects.csv` in this repository lists the 16 projects of the paper at the commits that were evaluated (Table 1).

2. Start the search:

   ```bash
   bash create_and_run_dockers.sh -p projects.csv
   ```

The first run builds the Docker image.
Each project then runs in its own container, where it is cloned, checked out at the given commit, built, and handed to the approach.

| Option | Meaning | Default |
| --- | --- | --- |
| `-p <file>` | The project list. | required |
| `-a <approach>` | `probo`, `boca` or `random_search`. | `probo` |
| `-t <seconds>` | Time budget per project; the search stops when it is used up. | the approach's default budget |
| `-jc <settings>` | JFR settings for PROBO's profiled runs: `default` or `profile` (the JDK's own), `custom` (the `custom.jfc` in this repository), or the path of a `.jfc` file inside the container. | `default` |

The experiments in the paper used the custom JFR settings, with each of the two budgets:

```bash
# Budget of 20 combinations (the default budget of PROBO)
bash create_and_run_dockers.sh -p projects.csv -a probo -jc custom

# Budget of one hour
bash create_and_run_dockers.sh -p projects.csv -a probo -jc custom -t 3600
```

## Output

Results are written to `results-java/<approach>/<repo>/` in this directory, for example `results-java/probo/jsoup/`.
If that directory exists, a timestamp is added to the name of the new one.

| File | Content | Written by |
| --- | --- | --- |
| `all_combinations_table.txt` | Every measured combination with its mean runtime, the reduction relative to the default configuration, and a Mann-Whitney U p-value. | all |
| `combination_mapping.json` | Maps each combination to the short name (`c1`, `c2`, ...) used in the other files. | all |
| `failing_combinations.txt` | Combinations that changed the outcome of a test. | all |
| `JVMBayesianOptimizer.log` | Log of the search. | all |
| `<owner>_<repo>.log` | Output of the container. | all |
| `verification_runs/` | Runtimes and per-test results of the 5 measuring runs of each combination. | PROBO |
| `comb_runs/` | PROBO: the same data for the profiled run of each combination. Baselines: the 5 measuring runs. | all |
| `parser_output/` | Runtime metrics extracted from the JFR recordings. | PROBO |
| `metric_importance.png`, `flag_importance.png` | Importance of the metrics and of the 10 most important flags in the final models. | PROBO |
| `c1/`, `c2/`, ... | Surefire reports of each run. | baselines |

## Running the best combination on later commits

`run_best_flags_for_all_projects.sh` measures whether a project's best flag combination stays effective as the project changes.
On each commit of a list, it runs the test suite 5 times under the default configuration and 5 times under the best combination.

```bash
bash run_best_flags_for_all_projects.sh -p projects.csv -c commits/ -b best_combinations.csv
```

| Option | Meaning | Default |
| --- | --- | --- |
| `-p <file>` | The project list, in the format above. Only the URL is used. | required |
| `-c <dir>` | A directory with one file `<repo>.csv` per project, with the columns `commit_number` and `sha`. The commits are run in the order of the file. | required |
| `-b <file>` | A CSV file with the columns `Project` (the `<repo>` name) and `Flags` (the combination, separated by spaces). | required |
| `-r <dir>` | Where the results are written. | `results-best-flags` |

The file of best combinations is not part of this repository, because every search gives its own result.
Write it from the `all_combinations_table.txt` of your own runs, for example:

```
Project,Flags
jsoup,-XX:+UseParallelGC -XX:-UsePerfData
commons-csv,-XX:TieredStopAtLevel=1
```

A project without a best combination is skipped.

`commits/` in this repository holds the commit lists of the paper's experiment: for each project, up to 100 commits after the one in `projects.csv`.

The results of a commit are in `<results-dir>/<repo>/<commit_number>/`:

| File | Content |
| --- | --- |
| `verification_runs/default.json` | Runtimes and per-test results of the runs under the default configuration. |
| `verification_runs/c_best.json` | The same for the best combination. |
| `commit.log` | The Maven output of every run, and the error if the commit could not be checked out. |

## Repository layout

| File | Purpose |
| --- | --- |
| `probo.py`, `boca.py`, `random_search.py` | The three approaches. |
| `evaluator.py` | Runs the test suite under a flag combination and records the results; shared by the approaches. |
| `utils.py` | The flag space, validation of combinations, and reading and writing of results. |
| `insert_jvm_args_in_pom.py` | Adds JVM arguments to a `pom.xml` that sets `argLine` itself, and restores it afterwards. |
| `jfr_parser/` | Extracts the runtime metrics from JFR recordings; see its [README](jfr_parser/README.md). |
| `custom.jfc` | The JFR settings used in the paper's experiments. |
| `projects.csv` | The 16 projects of the paper and their commits. |
| `commits/` | The later commits of each project that the best combination was run on. |
| `create_and_run_dockers.sh` | Entry point: starts one container per project. |
| `run_experiment.sh`, `run_maven.sh` | Run inside the container: build the project, run the approach, copy the results out. |
| `run_best_flags_for_all_projects.sh` | Entry point for the later-commits experiment: starts one container per project. |
| `run_best_flags_for_project.py` | Runs inside the container: runs the default configuration and the best combination on each commit. |
| `Dockerfile` | The Docker image. |

## Citation

```bibtex
@inproceedings{BazETAL2026ISSTA,
    author = {Baz, Abdelrahman and Lam, Wing and Shi, August},
    title = {Profiling-Guided {B}ayesian Optimization of {JVM} Configurations},
    booktitle = {International Symposium on Software Testing and Analysis},
    year = {2026},
    pages = {162:1--162:23},
    doi = {10.1145/3832253},
}
```
