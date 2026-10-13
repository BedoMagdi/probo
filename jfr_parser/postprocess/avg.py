"""Averages the metrics of each group's trials.

For every <root>/<group>/, reads <trial>/by_test_suite.csv (one "name,value" row per metric) of each
trial and writes <group>/avg_by_test_suite.csv, with one column per metric and a single row of means.

Usage: avg.py <root>
"""
import os
import sys

import pandas as pd


def average_trials_for_all(root: str):
    for group in os.listdir(root):
        group_dir = os.path.join(root, group)
        if not os.path.isdir(group_dir):
            continue
        trial_files = [os.path.join(group_dir, trial, 'by_test_suite.csv') for trial in os.listdir(group_dir)]
        dfs = [pd.read_csv(f, na_values='-', header=None) for f in trial_files if os.path.exists(f)]
        if dfs:
            dfs = [df.set_index(df.columns[0]).T for df in dfs]
            avg = pd.concat(dfs).mean(numeric_only=True).to_frame().T
            avg.to_csv(os.path.join(group_dir, 'avg_by_test_suite.csv'), index=False)


if __name__ == "__main__":
    average_trials_for_all(sys.argv[1])