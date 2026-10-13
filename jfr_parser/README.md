# JFR parser

Extracts the runtime metrics that guide PROBO's search from Java Flight Recorder (JFR) recordings.

`probo.py` runs the parser itself; the instructions below are for running it on its own.

## Running

```bash
cd jfr_parser
MVN=mvn ./run.sh <input_dir> [-d <output_dir>]
```

- Run it from this directory, and give both directories as absolute paths.
- It needs Java 11 or later.
- `MVN` is the Maven executable. It defaults to the Maven of the Docker image.
- Python 3 with pandas is needed for the averaging step.

## Input

One directory per recording, grouped by the configuration that was recorded:

```
<input_dir>
├── <group_1>/
│   ├── <trial_1>/
│   │   ├── <name>.jfr    # the JFR recording
│   │   └── gc.log        # optional, written with -Xlog:gc=debug:file=gc.log:time
│   └── ...               # further trials are optional
└── ...                   # further groups are optional
```

If a trial has a `gc.log`, the GC count and the GC pause time are read from it; otherwise they are computed from the JFR events.

## Output

```
<output_dir>
├── <group_1>/
│   ├── <trial_1>/
│   │   └── by_test_suite.csv    # the metrics of one recording, one "name,value" row per metric
│   └── avg_by_test_suite.csv    # the mean of each metric over the group's trials, one column per metric
└── ...
```

A metric whose JFR events are missing from the recording has the value `-` in `by_test_suite.csv` and is empty in `avg_by_test_suite.csv`.
A recording that cannot be read is reported and skipped.
