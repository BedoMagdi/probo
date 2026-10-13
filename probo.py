"""PROBO: profiling-guided Bayesian optimization of JVM flag combinations.

Each iteration evaluates flag combinations on a project's test suite, extracts runtime metrics
from a profiled run of each, trains models of flags -> metrics -> testing time on them, and uses
the models to choose the combinations of the next iteration (Section 3 of the paper).
"""
import os
import argparse
import subprocess
import shutil
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
import numpy as np
import itertools
from scipy.stats import norm
import matplotlib.pyplot as plt
import random
import logging
import traceback
import math
import time
from utils import *
from evaluator import Evaluator, NUM_RUNS

RANDOM_SEED = 10

# Search budget (Section 4.4 of the paper)
NUM_ITERATIONS = 6              # including the initial iteration of random combinations
NUM_INITIAL_COMBINATIONS = 5    # random combinations evaluated in the initial iteration, next to the default
COMBINATIONS_PER_ITERATION = 3  # t: combinations evaluated in each later iteration

# Model and candidate generation hyperparameters (Sections 3.1 and 3.3 of the paper)
CORRELATION_THRESHOLD = 0.3     # metrics whose |Kendall tau| with runtime is below this are dropped
TOP_K_FLAGS = 8                 # k: number of most important flags whose combinations are enumerated
INITIAL_NEIGHBORHOOD_SIZE = 30  # r0: neighbors generated per base combination before the decay starts
NEIGHBORHOOD_OFFSET = 10        # n0: number of evaluated combinations after which the decay starts
NEIGHBORHOOD_DECAY = 0.5        # decay
NEIGHBORHOOD_SCALE = 10         # scale

class Probo:
    """The models of PROBO and the generation of candidate combinations from them.

    The metrics models are one random forest per selected metric, each predicting the metric from
    the flags. The performance model is one random forest predicting the testing time from the
    metrics.
    """

    def __init__(self, all_flags, logger, seen_combs):
        self.combined_df = None
        
        self.metrics_models = {}  # Dict of metric name -> RF model
        self.performance_model = None
        
        self.seen_combs = seen_combs  # Set of already evaluated combinations
        self.all_flags = list(all_flags)  # a list keeps the flag order, and so the search, the same across runs
        self.logger = logger
        
        # For tracking important metrics and flags
        self.metric_importance = {}
        self.flag_importance = {}
        self.metric_scaler = None  # Scaler for metrics before feeding to runtime model

        """
            Stores the feature matrix for JVM flags
            Each row represents a specific test run
            Each column represents a specific JVM flag (1 if used, 0 if not)
            This is essentially a one-hot encoded representation of flag combinations
            example:
               has_-XX:+UseParallelGC  has_-XX:+UseG1GC  has_-XX:+AggressiveOpts  has_-XX:+DoEscapeAnalysis
            0                     1                  0                       0                           1
            1                     0                  1                       1                           0
            2                     1                  0                       1                           1
            3                     0                  0                       0                           1
            4                     0                  1                       0                           0
        """

        self.X_flags = None
        """
            Stores the matrix of JVM metrics
            Each row corresponds to a test run
            Each column represents a different metric (e.g., "GC count", "User CPU % mean")
            These are the actual measurements collected from JFR files
            example of self.X_metrics[metric]:
            0    15.3
            1     8.2
            2    12.7
            3    18.9
            4     6.4
        """
        self.X_metrics = None
        """
            Stores the target variable
            Contains the normalized runtime for each test suite run
            This is what the system ultimately tries to minimize
        """
        self.y_runtime = None
    
    def update_model(self, data_dir, dir_to_comb, runtimes):
        """Retrain the models on the JFR parser output in data_dir.

        dir_to_comb maps each directory in data_dir to its flag combination, and runtimes maps
        each combination to its measured runtimes.
        """
        self.combined_df = self._load_data(data_dir, dir_to_comb)

        self._preprocess_and_fit_models(runtimes)
        self._update_metric_importance()
        self._update_flag_importance()
        
    def _load_data(self, data_dir, dir_to_comb):
        """Load data from JFR parser output"""
        csv_files = []
        for root, dirs, files in os.walk(data_dir):
            if root == data_dir:
                dirs[:] = [d for d in dirs if d not in ('html', 'all')]
            for file in files:
                if file == "avg_by_test_suite.csv":
                    csv_files.append(os.path.join(root, file))

        self.logger.info(f"Loading data from {len(csv_files)} CSV files")
        
        dfs = []
        for file in csv_files:
            self.logger.info(f"Loading data from file: {file}")
            dir_name = file.split('/')[-2]  # we follow a fixed directory structure (dir_name/avg_by_test_suite.csv)
            df = pd.read_csv(file)
            
            if dir_name in dir_to_comb:
                flag_combination = dir_to_comb[dir_name]
                self.seen_combs.add(flag_combination)
            elif dir_name == "default":
                flag_combination = "default"
            else:
                self.logger.warning(f"Unknown directory {dir_name}, skipping file {file}")
                continue
            
            df['Flag'] = flag_combination
            dfs.append(df)
        
        return pd.concat(dfs, ignore_index=True)
    
    def _preprocess_and_fit_models(self, runtimes):
        """Preprocess data and fit both models"""
        if self.combined_df is None or self.combined_df.empty:
            self.logger.warning("No data available for preprocessing")
            return
        
        try:
            df_clean = self.combined_df.copy()
            
            if df_clean.empty:
                self.logger.warning("No valid data after preprocessing")
                return
                
            # Create flag features for ALL flags in search space (not just seen ones)
            for flag in self.all_flags:
                df_clean[f'has_{flag}'] = df_clean['Flag'].apply(
                    lambda x: 1 if isinstance(x, str) and flag in x.split() else 0
                )
            
            mandatory_cols_to_drop = ['Flag', 'Duration (s)']
            
            flag_cols = [col for col in df_clean.columns if col.startswith('has_')]
            self.X_flags = df_clean[flag_cols].fillna(0)
            
            # Get target variable first (needed for correlation calculation)
            df_clean["Runtime"] = df_clean["Flag"].apply(lambda flag: np.mean(runtimes[flag]) if runtimes.get(flag) else np.nan)
            self.y_runtime = df_clean['Runtime']

            if self.y_runtime.isna().any():
                num_nans = self.y_runtime.isna().sum()
                self.logger.warning(f"Detected {num_nans} NaN values in y_runtime. These rows will be excluded from training.")
                
                valid_idx = self.y_runtime.notna()
                self.X_flags = self.X_flags[valid_idx].reset_index(drop=True)
                df_clean = df_clean[valid_idx].reset_index(drop=True)
                self.y_runtime = self.y_runtime[valid_idx].reset_index(drop=True)
            
            # Get potential metric features (excluding mandatory drops and flag features)
            potential_metric_cols = [col for col in df_clean.columns 
                                if col not in mandatory_cols_to_drop 
                                and not col.startswith('has_')
                                and col != 'Runtime']  # Also exclude the target

            # Remove columns that are all NaN
            all_nan_cols = [col for col in potential_metric_cols if df_clean[col].isna().all()]
            if all_nan_cols:
                self.logger.warning(f"Removing {len(all_nan_cols)} columns with all NaN values: {all_nan_cols}")
                potential_metric_cols = [col for col in potential_metric_cols if col not in all_nan_cols]
            
            # Create temporary metrics dataframe for correlation calculation
            temp_metrics = df_clean[potential_metric_cols].copy()
            
            # Handle infinity and extreme values for correlation calculation
            temp_metrics = temp_metrics.replace([np.inf, -np.inf], np.nan)
            medians = temp_metrics.median().fillna(0)
            temp_metrics = temp_metrics.fillna(medians)
            
            # Clip extreme values
            max_val = np.finfo(np.float32).max / 1000
            min_val = -max_val
            for col in temp_metrics.columns:
                temp_metrics[col] = np.clip(temp_metrics[col], min_val, max_val)
            
            # Calculate correlation between each metric and runtime
            correlations = {}
            
            for col in temp_metrics.columns:
                # Check if the column has varying values
                if temp_metrics[col].nunique() > 1:
                    try:
                        correlation = abs(temp_metrics[col].corr(self.y_runtime, method='kendall'))
                        # Replace NaN with 0
                        correlations[col] = 0.0 if np.isnan(correlation) else correlation
                    except Exception as e:
                        self.logger.warning(f"Could not calculate correlation for {col}: {e}")
                        correlations[col] = 0.0
                else:
                    # For constant columns, set correlation to 0
                    self.logger.warning(f"Column {col} has constant values, setting correlation to 0")
                    correlations[col] = 0.0
            
            # Filter metrics based on correlation threshold
            selected_metric_cols = [col for col, corr in correlations.items() 
                                if corr >= CORRELATION_THRESHOLD]
            
            dropped_weak_corr_cols = [col for col, corr in correlations.items() 
                                    if corr < CORRELATION_THRESHOLD]
            
            self.logger.info("Correlation analysis complete:")
            self.logger.info(f"  - Total potential metrics: {len(potential_metric_cols)}")
            self.logger.info(f"  - Selected metrics (correlation >= {CORRELATION_THRESHOLD}): {len(selected_metric_cols)}")
            self.logger.info(f"  - Dropped weak correlation metrics: {len(dropped_weak_corr_cols)}")
            
            # Log top correlated metrics
            sorted_correlations = sorted(correlations.items(), key=lambda x: x[1], reverse=True)
            top_correlated = sorted_correlations[:10]
            self.logger.info(f"Top 10 correlated metrics: {top_correlated}")
            
            if dropped_weak_corr_cols:
                self.logger.info(f"Dropped metrics with weak correlations: {dropped_weak_corr_cols}")
            
            # If no metrics pass the threshold, keep at least the top 5 most correlated ones
            if not selected_metric_cols:
                self.logger.warning(f"No metrics above correlation threshold {CORRELATION_THRESHOLD}. Keeping top 5.")
                selected_metric_cols = [col for col, _ in sorted_correlations[:5]]
            
            # Create final metrics dataframe
            self.X_metrics = df_clean[selected_metric_cols].copy()
            
            # Apply the same cleaning to the final metrics
            self.X_metrics = self.X_metrics.replace([np.inf, -np.inf], np.nan)
            medians = self.X_metrics.median().fillna(0)
            self.X_metrics = self.X_metrics.fillna(medians)
            
            # Clip extreme values
            for col in self.X_metrics.columns:
                self.X_metrics[col] = np.clip(self.X_metrics[col], min_val, max_val)
            
            # Final check: ensure no infinity or NaN values remain
            if np.any(np.isinf(self.X_metrics.values)) or np.any(np.isnan(self.X_metrics.values)):
                self.logger.error("Still have infinity or NaN values after cleaning")
                self.X_metrics = self.X_metrics.replace([np.inf, -np.inf, np.nan], 0)
            
            self.logger.info(f"Data cleaning complete. X_metrics shape: {self.X_metrics.shape}")
            self.logger.info(f"Final selected metrics: {list(self.X_metrics.columns)}")

            self.logger.debug(f"df_clean sample:\n{df_clean.head()}")
            self.logger.debug(f"X_flags sample:\n{self.X_flags.head()}")
            self.logger.debug(f"X_metrics sample:\n{self.X_metrics.head()}")
            self.logger.debug(f"y_runtime sample:\n{self.y_runtime.head()}")

            # Clear old models before creating new ones
            self.metrics_models.clear()

            # Create and fit scaler for metrics
            self.metric_scaler = StandardScaler()
            self.X_metrics_scaled = self.metric_scaler.fit_transform(self.X_metrics)

            # Fit RF models for each metric
            for metric in self.X_metrics.columns:
                try:
                    self.metrics_models[metric] = RandomForestRegressor(
                            n_estimators=100, random_state=42)

                    self.metrics_models[metric].fit(self.X_flags, self.X_metrics[metric])
                    
                except Exception as e:
                    self.logger.error(f"Error fitting RF model for {metric}: {e}")
                    
            # Fit the runtime model
            self.performance_model = RandomForestRegressor(
                            n_estimators=100, random_state=42)
            self.performance_model.fit(self.X_metrics_scaled, self.y_runtime)
            
            self.logger.info(f"Preprocessed data shapes: X_flags={self.X_flags.shape}, X_metrics={self.X_metrics.shape}")
            
        except Exception as e:
            self.logger.error(f"Error in preprocessing and fitting: {str(e)}")
            self.logger.error(traceback.format_exc())

    def _update_metric_importance(self):
        """Take each metric's importance from the performance model's feature importances."""
        if self.X_metrics is None or self.y_runtime is None or self.performance_model is None:
            return
            
        # Get feature importances from the runtime prediction model
        importances = self.performance_model.feature_importances_

        # Map to metric names
        metric_importance = {}
        for i, metric in enumerate(self.X_metrics.columns):
            metric_importance[metric] = importances[i]

        # Update metric importance
        self.metric_importance = metric_importance
        top_metrics = sorted(self.metric_importance.items(), key=lambda x: x[1], reverse=True)[:5]
        self.logger.info(f"Top 5 important metrics: {top_metrics}")

    def _update_flag_importance(self):
        """Update flag importance: each flag's importance for a metric, weighted by that metric's importance for runtime"""

        if not self.metrics_models or self.X_flags is None or self.X_metrics is None:
            return

        try:
            flag_importance = {flag.replace('has_', ''): 0.0 for flag in self.X_flags.columns}

            for metric, model in self.metrics_models.items():
                metric_weight = self.metric_importance.get(metric, 0.0)
                for i, flag_col in enumerate(self.X_flags.columns):
                    flag_name = flag_col.replace('has_', '')
                    flag_importance[flag_name] += model.feature_importances_[i] * metric_weight

            # Normalize so that the importances sum to 1
            self.flag_importance = flag_importance
            total = np.sum(list(flag_importance.values()))
            if total > 0:
                self.flag_importance = {k: v/total for k, v in flag_importance.items()}

            top_flags_by_importance = sorted(self.flag_importance.items(), key=lambda x: x[1], reverse=True)[:10]
            self.logger.info(f"Top 10 flags by importance: {top_flags_by_importance}")

        except Exception as e:
            self.logger.error(f"Error updating flag importance: {e}")
            
    def get_batch_expected_improvements(self, flag_combinations):
        """Calculate EI for all flag combinations at once using vectorization"""
        if not self.metrics_models or self.performance_model is None:
            return [0.0] * len(flag_combinations)
        
        try:
            # 1. Create a single NumPy array for all flag combinations
            flag_array = np.zeros((len(flag_combinations), len(self.X_flags.columns)))
            
            # Fill the flag array for all combinations at once
            for i, comb_str in enumerate(flag_combinations):
                flags = comb_str.split()
                for j, flag_col in enumerate(self.X_flags.columns):
                    flag_name = flag_col.replace('has_', '')
                    if flag_name in flags:
                        flag_array[i, j] = 1.0
            
            # 2. Predict metrics from flags in one pass for each model
            metrics_array = np.zeros((len(flag_combinations), len(self.X_metrics.columns)))
            
            for j, metric in enumerate(self.X_metrics.columns):
                if metric in self.metrics_models:
                    metrics_array[:, j] = self.metrics_models[metric].predict(flag_array)
                else:
                    metrics_array[:, j] = self.X_metrics[metric].median()
            
            # 3. Get predictions from all trees for all candidates
            # Scale the predicted metrics using the same scaler from training
            if hasattr(self, 'metric_scaler'):
                metrics_array_scaled = self.metric_scaler.transform(metrics_array)
            else:
                self.logger.warning("No metric scaler found, using unscaled metrics")
                metrics_array_scaled = metrics_array

            all_predictions = []
            for estimator in self.performance_model.estimators_:
                all_predictions.append(estimator.predict(metrics_array_scaled))
            
            # 4. Convert to NumPy array and transpose
            # Shape before: [n_trees, n_candidates] → after: [n_candidates, n_trees]
            pred = np.array(all_predictions).transpose()
            
            # 5. Calculate mean and std for each candidate
            mu = np.mean(pred, axis=1)
            sigma = np.std(pred, axis=1)
            
            # 6. Calculate EI using vectorized operations
            y_best = np.min(self.y_runtime)
            
            def calculate_ei():
                z = (y_best - mu) / sigma
                return (y_best - mu) * norm.cdf(z) + sigma * norm.pdf(z)
            
            # Handle zero variance
            if np.any(sigma == 0.0):
                sigma_copy = np.copy(sigma)
                sigma[sigma_copy == 0.0] = 1.0  # Temporarily set to 1.0
                ei_values = calculate_ei()
                ei_values[sigma_copy == 0.0] = 0.0  # Reset to 0 where sigma was 0
            else:
                ei_values = calculate_ei()
            
            return ei_values.tolist()
            
        except Exception as e:
            self.logger.error(f"Error in batch EI calculation: {e}")
            return [0.0] * len(flag_combinations)


    def get_pure_systematic_combinations(self, top_flags=None, exclude_set=set()):
        """Strategy 3: return all valid non-empty subsets of the top-k flags that were not evaluated yet."""
        all_combinations = set()  # Use set to automatically handle duplicates
        
        if top_flags is None:
            # Fallback to current logic if not provided
            flag_importances = [(flag_col.replace('has_', ''), self.flag_importance.get(flag_col.replace('has_', ''), 0.0)) 
                                for flag_col in self.X_flags.columns]
            top_important_flags = [flag for flag, _ in sorted(flag_importances, key=lambda x: x[1], reverse=True)[:TOP_K_FLAGS]]
        else:
            # Convert from indices back to flag names if needed
            top_important_flags = [self.X_flags.columns[i].replace('has_', '') for i, _ in top_flags]
        
        # Generate all combinations of top important flags (sizes 1 to n)
        for size in range(1, len(top_important_flags) + 1):
            for combo in itertools.combinations(top_important_flags, size):
                valid_combo = validate_combination(list(combo))
                if valid_combo:
                    all_combinations.add(' '.join(sorted(valid_combo)))
        
        # Filter out already seen combinations and convert to list
        filtered_combinations = [comb for comb in sorted(all_combinations) if comb not in self.seen_combs and comb not in exclude_set]
        
        self.logger.info(f"Generated {len(all_combinations)} total systematic combinations, "
                        f"{len(filtered_combinations)} new ones")
        
        return filtered_combinations

    def get_optimized_combinations(self, num_combinations):
        """Generate optimized flag combinations using efficient search based on feature importance"""
        if not self.metrics_models or len(self.y_runtime) < 1:
            self.logger.info("Not enough data for model-based search, using random combinations")
            self.logger.info(f"metrics_models keys: {list(self.metrics_models.keys())}\n len(self.y_runtime): {len(self.y_runtime)}")
            return self._get_weighted_random_combinations(num_combinations)
        
        combs_ready = []

        # Use the top k flags, or fewer if the flag space is smaller
        available_flags = len(self.X_flags.columns)
        top_k = min(available_flags, TOP_K_FLAGS)
        top_k = max(top_k, 1)  # Ensure at least 1 flag
        
        # Extract feature importances from flag_importance
        flag_importances = []
        for i, flag_col in enumerate(self.X_flags.columns):
            flag_name = flag_col.replace('has_', '')
            importance = self.flag_importance.get(flag_name, 0.0)
            flag_importances.append([i, importance])
        
        # Sort features by importance and select the top k features
        flag_selected = sorted(flag_importances, key=lambda x: x[1], reverse=True)[:top_k]
        flag_ids = [i for i, _ in flag_importances]  # All flag indices
        
        # Calculate the neighborhood size, which decays as more combinations are evaluated
        sigma = -NEIGHBORHOOD_SCALE ** 2 / (2 * math.log(NEIGHBORHOOD_DECAY))
        neighborhood_size = INITIAL_NEIGHBORHOOD_SIZE * math.exp(
            -max(0, len(self.y_runtime) - NEIGHBORHOOD_OFFSET) ** 2 / (2 * sigma ** 2))

        self.logger.info(f"Neighborhood size calculated as {neighborhood_size} (based on {len(self.y_runtime)} evaluated combinations)")

        # Generate all possible combinations of important flags (2^k)
        num_columns = len(self.X_flags.columns)
    
        class FlagExchanger:
            def __init__(self, incumbent, num_columns):
                self.incumbent = incumbent
                self.num_columns = num_columns
                
            def to_next(self, feature_ids):
                ans = [0] * self.num_columns
                # Set selected feature IDs to 1
                for f in feature_ids:
                    ans[f] = 1
                # Apply incumbent configuration
                for f in self.incumbent:
                    ans[f[0]] = f[1]
                return ans
        
        # Create base configurations (neighborhood iterators)
        neighborhood_iterators = []
        for i in range(2 ** top_k):
            # Convert i to binary representation
            comb = bin(i).replace('0b', '')
            comb = '0' * (top_k - len(comb)) + comb
            # Create incumbent configuration
            inc = []
            for k, s in enumerate(comb):
                if s == '1':
                    inc.append((flag_selected[k][0], 1))
                else:
                    inc.append((flag_selected[k][0], 0))
            neighborhood_iterators.append(FlagExchanger(inc, num_columns))
        
        self.logger.info(f"Generated {len(neighborhood_iterators)} base combinations")
        
        # Generate neighbors
        neighbors = []
        for i, inc in enumerate(neighborhood_iterators):
            # Convert the neighborhood size to int, ensuring at least 1 neighbor
            for j in range(1 + int(neighborhood_size)):
                # Randomly select a subset of features
                num_features = random.randint(0, len(flag_ids))
                selected_feature_ids = random.sample(flag_ids, num_features)
                # Generate new configuration
                n = inc.to_next(selected_feature_ids)
                neighbors.append(n)
        
        self.logger.info(f"Generated {len(neighbors)} candidates using neighborhood size={neighborhood_size}")

        # Convert binary flag representations to actual flag strings
        candidate_sources = {}
        candidates = []
        candidates_set = set()
        for neighbor in neighbors:
            # Convert binary representation to flag names
            selected_flags = []
            for i, val in enumerate(neighbor):
                if val == 1:
                    flag_name = self.X_flags.columns[i].replace('has_', '')
                    selected_flags.append(flag_name)
            
            # Validate combination
            valid_comb = validate_combination(selected_flags)
            comb_str = ' '.join(sorted(valid_comb))
            
            if comb_str and comb_str not in self.seen_combs and comb_str not in candidates_set:
                candidates.append(comb_str)
                candidates_set.add(comb_str)
                candidate_sources[comb_str] = "systematic"

        self.logger.info(f"Generated {len(candidates)} systematic candidates")
        
        # Generate random combinations
        num_random_candidates = len(candidates)

        weighted_random_combs = self._get_weighted_random_combinations(num_random_candidates, candidates_set)
        for comb_str in weighted_random_combs:
            candidate_sources[comb_str] = "weighted_random"      

        self.logger.info(f"Generated {len(weighted_random_combs)} weighted random candidates")
        candidates.extend(weighted_random_combs)
        candidates_set.update(weighted_random_combs) # Update the set as well

        pure_systematic_combinations = self.get_pure_systematic_combinations(top_flags=flag_selected, exclude_set=candidates_set)
        for comb_str in pure_systematic_combinations:
            candidate_sources[comb_str] = "pure_systematic"

        self.logger.info(f"Generated {len(pure_systematic_combinations)} pure_systematic candidates")
        candidates.extend(pure_systematic_combinations)

        num_generated_candidates = len(candidates)
        self.logger.info(f"Generated total of {num_generated_candidates} candidates for optimization")
        print(f"Generated {num_generated_candidates} candidates for optimization", flush=True)
        
        start_time = time.time()

        # Process all candidates in one batch call
        batch_ei_values = self.get_batch_expected_improvements(candidates)
    
        # Create evaluated candidates list
        evaluated_candidates = [(comb, ei, candidate_sources[comb]) 
                for comb, ei in zip(candidates, batch_ei_values)]

        self.logger.info(f"Evaluated {len(evaluated_candidates)} candidates for optimization")
        end_time = time.time()
        elapsed_time = end_time - start_time
        self.logger.info(f"EI calculation total time: {elapsed_time:.2f} seconds")

        evaluated_candidates.sort(key=lambda x: x[1], reverse=True)
        for i, (comb_str, ei, source) in enumerate(evaluated_candidates[:num_combinations]):
            self.logger.info(f"Rank {i+1}: ei: {ei:.6f}, Source: {source}, Comb: {comb_str}")
            combs_ready.append(comb_str)
        
        # If we couldn't find enough combinations, fall back
        if len(combs_ready) < num_combinations:
            self.logger.info(f"Generated only {len(combs_ready)}/{num_combinations} combinations, using fallback")
            additional_combs = self._get_weighted_random_combinations(
                num_combinations - len(combs_ready)
            )
            combs_ready.extend(additional_combs)
        
        return combs_ready

    def _get_weighted_random_combinations(self, num_combinations, exclude_set=set()):
        """Strategy 2: sample combinations whose flags are drawn with probability proportional to their importance."""
        combs_ready = []
        flags = list(self.all_flags)
        
        # Use computed flag importance if available
        if self.flag_importance and any(self.flag_importance.values()):
            weights = np.array([self.flag_importance.get(f, 0.0) for f in flags])
            
            # Add small uniform component to prevent zero-weight deadlock
            uniform_weight = 0.1 / len(flags)
            weights = weights + uniform_weight
            
            # Normalize to sum to 1
            weights = weights / np.sum(weights)
        else:
            # Fallback to uniform
            weights = np.ones(len(flags)) / len(flags)
            
        # Count non-zero entries for safety
        non_zero_count = np.sum(weights > 1e-10)
        max_combination_size = min(len(flags), non_zero_count)
        
        while len(combs_ready) < num_combinations:
            # Weighted sampling with variable combination size
            combination_size = random.randint(1, max_combination_size)
            
            combination = np.random.choice(
                flags, 
                size=combination_size, 
                replace=False,
                p=weights
            ).tolist()
            
            valid_comb = validate_combination(combination)
            comb_str = ' '.join(sorted(valid_comb))
            
            if (comb_str and 
                comb_str not in self.seen_combs and 
                comb_str not in exclude_set and
                comb_str not in combs_ready):
                combs_ready.append(comb_str)
        
        return combs_ready


class ProfilingEvaluator(Evaluator):
    """Evaluates a combination with one profiled run, which records the JFR data that the metrics
    are extracted from, followed by NUM_RUNS unprofiled runs that measure its runtime."""

    def __init__(self, project_root_dir, res_dir, modified_slug, jfc_file, insert_jvm_args_in_pom_path, logger, deadline=None):
        super().__init__(project_root_dir, res_dir, insert_jvm_args_in_pom_path, logger, deadline)
        self.modified_slug = modified_slug
        self.jfc_file = jfc_file

    def _evaluate(self, comb, dir_name, timeout):
        self._profile(comb, dir_name, timeout)
        return self._measure(comb, dir_name, timeout)

    def _profile(self, comb, dir_name, timeout):
        """Run the test suite once with JFR enabled and save the recording for the JFR parser."""
        jfr_file = os.path.join(self.project_root_dir, f"{self.modified_slug}_flight_1.jfr")
        gc_log = os.path.join(self.project_root_dir, "gc.log")
        for path in (jfr_file, gc_log):
            if os.path.exists(path):
                os.remove(path)

        jvm_args = [
            "-XX:+FlightRecorder",
            f"-XX:StartFlightRecording=filename={os.path.basename(jfr_file)},settings={self.jfc_file}",
            "-XX:FlightRecorderOptions=stackdepth=1024",
        ]
        if comb != "default":
            jvm_args.append(comb)
        jvm_args.append("-Xlog:gc=debug:file=gc.log:time")

        try:
            mvn_time, report = self._run_tests(' '.join(jvm_args), timeout)
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Combination {comb} profiling timed out after {timeout} seconds")
            return

        if comb == "default" and self.default_mvn_runtime is None and mvn_time is not None:
            self.default_mvn_runtime = mvn_time
            self.logger.info(f"Default runtime detected: {mvn_time} seconds")

        if report is None:
            self.logger.warning(f"Combination {comb} profiling failed - no surefire reports found")
            return
        runtime, summary, test_times = report

        if comb == "default" and self.default_test_results is None:
            self.default_test_results = {test: info['result'] for test, info in test_times.items()}

        # Save the input of the JFR parser
        dest = os.path.join(self.res_dir, "parser_input", dir_name, "1")
        os.makedirs(dest, exist_ok=True)
        for path in (jfr_file, gc_log):
            if os.path.exists(path):
                shutil.copy(path, dest)
        shutil.copytree(self.surefire_reports_dir, os.path.join(dest, "surefire-reports"), dirs_exist_ok=True)

        out_dir = os.path.join(self.res_dir, "comb_runs")
        os.makedirs(out_dir, exist_ok=True)
        write_to_json([runtime], [summary], [test_times], os.path.join(out_dir, f"{dir_name}.json"))

    def _measure(self, comb, dir_name, timeout):
        """Measure the runtime of a combination over NUM_RUNS unprofiled runs.

        A run only counts if every test has the same outcome as under the default configuration.
        Returns True if the runtime was measured.
        """
        # The default configuration gets extra attempts, because the other combinations depend on it
        max_attempts = (NUM_RUNS + 1) * (3 if comb == "default" else 1)
        runtimes, summaries, test_times_lst = [], [], []

        for attempt in range(1, max_attempts + 1):
            if len(runtimes) == NUM_RUNS:
                break
            try:
                _, report = self._run_tests("" if comb == "default" else comb, timeout)
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Combination {comb} timed out after {timeout} seconds")
                return False

            if report is None:
                self.logger.warning(f"Combination {comb} run {attempt} failed - no surefire reports found")
                if comb != "default":
                    return False
                continue
            runtime, summary, test_times = report

            test_results = {test: info['result'] for test, info in test_times.items()}
            if self.default_test_results and test_results != self.default_test_results:
                if comb != "default":
                    self.failing_combinations.append(comb)
                    self.logger.warning(f"Combination '{comb}' failed with different test results")
                    return False
                self.logger.warning(f"Default combination run {attempt} had different results, retrying...")
                continue

            runtimes.append(runtime)
            summaries.append(summary)
            test_times_lst.append(test_times)
            self.logger.info(f"Combination {comb} successful run {len(runtimes)} with runtime: {runtime}")

        if len(runtimes) < NUM_RUNS:
            self.logger.warning(f"Combination {comb} could not achieve {NUM_RUNS} consistent runs after {max_attempts} attempts")
            return False

        self.runtimes[comb] = runtimes
        out_dir = os.path.join(self.res_dir, "verification_runs")
        os.makedirs(out_dir, exist_ok=True)
        write_to_json(runtimes, summaries, test_times_lst, os.path.join(out_dir, f"{dir_name}.json"))
        return True


def format_elapsed_time(seconds):
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {seconds:.2f}s"


def execute(project_path, res_dir, modified_slug, jfc_file, insert_jvm_args_in_pom_path, JFR_PARSER_path, time_budget=None):
    """Run PROBO on the Maven project in project_path and write the results to res_dir.

    The search runs for NUM_ITERATIONS iterations, or until time_budget seconds have passed if a
    budget is given.
    """
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

    seen = set()  # combinations that were already tried
    deadline = None
    if time_budget is not None:
        deadline = start_time + time_budget
        logger.info(f"Overall time limit = {time_budget}s")
    evaluator = ProfilingEvaluator(project_path, res_dir, modified_slug, jfc_file, insert_jvm_args_in_pom_path, logger, deadline)
    optimizer = Probo(flag_space, logger, seen)
    parser_input_dir = os.path.join(res_dir, "parser_input")
    parser_output_dir = os.path.join(res_dir, "parser_output")
    os.makedirs(parser_output_dir, exist_ok=True)

    successful_combinations = set()
    iteration = 0
    # With a time budget the search ends when the time is up; without one, after NUM_ITERATIONS iterations
    while (time_budget is not None or iteration < NUM_ITERATIONS) and not evaluator.out_of_time():
        iteration_start_time = time.time()
        logger.info(f"Starting iteration {iteration + 1}")

        if iteration == 0:
            # Step 1: evaluate the default configuration and random combinations. This is repeated
            # until enough of them were evaluated successfully to train the models.
            if "default" not in successful_combinations:
                combs = [''] + get_random_combinations(flag_space, NUM_INITIAL_COMBINATIONS, len(flag_space), seen)
            else:
                num_missing = NUM_INITIAL_COMBINATIONS + 1 - len(successful_combinations)
                combs = get_random_combinations(flag_space, num_missing, len(flag_space), seen)
            logger.info(f"Generated random combinations: {combs}")
            successful = evaluator.run_combs(combs)
            seen.update(successful)
        else:
            # Steps 4 and 5: generate candidates and rank them by expected improvement
            candidates = optimizer.get_optimized_combinations(COMBINATIONS_PER_ITERATION * 4)
            logger.info(f"Selected candidates: {candidates}")

            # Step 6: evaluate the candidates in that order until enough of them are successful
            successful, num_tried = [], 0
            while len(successful) < COMBINATIONS_PER_ITERATION and num_tried < len(candidates) and not evaluator.out_of_time():
                batch = candidates[num_tried:num_tried + COMBINATIONS_PER_ITERATION - len(successful)]
                successful += evaluator.run_combs(batch)
                num_tried += len(batch)
            seen.update(candidates[:num_tried])

        successful_combinations.update(successful)
        logger.info(f"Successful combinations so far ({len(successful_combinations)}): {successful_combinations}")

        if len(successful_combinations) >= NUM_INITIAL_COMBINATIONS + 1:
            # Extract the runtime metrics from the new JFR recordings
            parser_cmd = ["bash", "run.sh", parser_input_dir, "-d", parser_output_dir]
            logger.info(f"Running command: {' '.join(parser_cmd)}")
            try:
                result = subprocess.run(parser_cmd, cwd=JFR_PARSER_path, check=True, capture_output=True, text=True)
                print(result.stdout)
            except subprocess.CalledProcessError as e:
                print(e.stdout)
                logger.error(f"Parser error: {e}\n{e.stderr}")
            shutil.rmtree(parser_input_dir, ignore_errors=True)

            # Steps 2 and 3: retrain the models and recompute the flag importance
            optimizer.update_model(parser_output_dir, evaluator.dir_to_comb, evaluator.runtimes)
            iteration += 1

        logger.info(f"Iteration completed in {format_elapsed_time(time.time() - iteration_start_time)}")
        logger.info(f"Elapsed time since start: {format_elapsed_time(time.time() - start_time)}")

    evaluator.save_results()

    # Plot the importance of the metrics and of the 10 most important flags
    top_flags = dict(sorted(optimizer.flag_importance.items(), key=lambda x: x[1])[-10:])
    for file_name, title, importance in [("metric_importance.png", "Metric Importance", optimizer.metric_importance),
                                         ("flag_importance.png", "Top 10 Most Important Flags", top_flags)]:
        if importance:
            names = sorted(importance, key=importance.get)
            plt.figure(figsize=(12, 8))
            plt.barh(names, [importance[name] for name in names])
            plt.title(title)
            plt.tight_layout()
            plt.savefig(os.path.join(res_dir, file_name))
            plt.close()

    logger.info(f"Total execution time: {format_elapsed_time(time.time() - start_time)}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Search for JVM flag combinations that reduce the test-suite runtime of a Maven project.")
    parser.add_argument("--project-dir", required=True, help="directory of the Maven project (or module) whose tests are run")
    parser.add_argument("--results-dir", required=True, help="directory to write the results to")
    parser.add_argument("--slug", required=True, help="project name used for naming the JFR recordings, e.g. owner_repo")
    parser.add_argument("--jfc-file", default="default.jfc", help="JFR settings: default.jfc, profile.jfc, or the path to a .jfc file (default: %(default)s)")
    parser.add_argument("--insert-script", required=True, help="path to insert_jvm_args_in_pom.py")
    parser.add_argument("--jfr-parser-dir", required=True, help="directory of the JFR parser (contains run.sh)")
    parser.add_argument("--time-budget", type=float, help="search for this many seconds instead of for a fixed number of iterations")
    args = parser.parse_args()

    execute(args.project_dir, args.results_dir, args.slug, args.jfc_file, args.insert_script, args.jfr_parser_dir, args.time_budget)
