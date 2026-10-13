import random
import numpy as np
import os
import json
import xml.etree.ElementTree as ET
import re
from tabulate import tabulate
from scipy import stats

FLAGS = ['-XX:-C1UpdateMethodData', '-XX:-C1ProfileCalls', '-XX:-C1ProfileInlinedCalls', '-XX:-C1ProfileBranches', '-XX:-C1OptimizeVirtualCallProfiling',
         '-XX:TieredStopAtLevel=2',
         '-XX:+UseSerialGC', '-XX:+UseParallelGC', '-XX:+UseCondCardMark', '-XX:-UseSuperWord', "-XX:+AggressiveHeap","-XX:+AlwaysPreTouch", # default GC in java11 is UseG1GC
         '-XX:G1HeapRegionSize=16m', '-XX:G1HeapRegionSize=32m', # The default region size is determined ergonomically based on the heap size. In container, default is 1mb (min), use 16 and 32mb (max)
         '-XX:G1ReservePercent=5', '-XX:G1ReservePercent=20', # default is 10
         "-XX:InitiatingHeapOccupancyPercent=22", "-XX:InitiatingHeapOccupancyPercent=90", # default is 45
         "-XX:MaxHeapFreeRatio=0", "-XX:MaxHeapFreeRatio=35", "-XX:MaxHeapFreeRatio=100", # default is 70 in container with java11, 8gb mem, 2 cpus, ubuntu:22.04
         "-XX:MinHeapFreeRatio=0", "-XX:MinHeapFreeRatio=20", "-XX:MinHeapFreeRatio=80", # default is 40 in container with java11, 8gb mem, 2 cpus, ubuntu:22.04
         "-XX:NewRatio=1", "-XX:NewRatio=4", # default is 2 
         "-XX:ParallelGCThreads=6", "-XX:ParallelGCThreads=26", # default is 13
         "-XX:+ScavengeBeforeFullGC", 
         "-XX:SurvivorRatio=4", "-XX:SurvivorRatio=16", # default is 8
         "-XX:TargetSurvivorRatio=25", "-XX:TargetSurvivorRatio=100", # default is 50
         "-XX:-UseAdaptiveSizePolicy",'-XX:+UseStringDeduplication', "-XX:-UseTLAB", 
         '-XX:AllocatePrefetchStyle=2', "-XX:AllocatePrefetchStyle=0", "-XX:AllocatePrefetchStyle=3", # takes 4 values, default is 1
         "-Xnoclassgc", "-Xshare:off", 
         "-XX:CICompilerCount=6", "-XX:CICompilerCount=24", #  default = 12
         "-XX:-TieredCompilation", "-XX:-OptimizeStringConcat",
         "-XX:-ParallelRefProcEnabled", "-XX:-DoEscapeAnalysis", "-XX:+UseTransparentHugePages", 
         "-XX:AllocateInstancePrefetchLines=2", "-XX:AllocateInstancePrefetchLines=4", # By default, the number of lines to prefetch is set to 1:
         "-XX:AllocatePrefetchInstr=0", "-XX:AllocatePrefetchInstr=1", "-XX:AllocatePrefetchInstr=2", #Possible values are from 0 to 3. The actual instructions behind the values depend on the platform. By default, the prefetch instruction is set to 3.
         "-XX:AllocatePrefetchLines=2", "-XX:AllocatePrefetchLines=8", # default = 4, The default value is 1 if the last allocated object was an instance, and 3 if it was an array.
         "-XX:AllocatePrefetchStepSize=32", "-XX:AllocatePrefetchStepSize=128", #  By default, the step size is set to 64 bytes:
         '-XX:-UsePerfData', "-XX:-UseCompressedOops",
         "-XX:ObjectAlignmentInBytes=64", "-XX:ObjectAlignmentInBytes=32", "-XX:ObjectAlignmentInBytes=16", # 8 is minimum and is the default, so use 16 and 32, 64?
         '-XX:ReservedCodeCacheSize=120M', '-XX:ReservedCodeCacheSize=480M', # default 240M
         '-XX:MaxGCPauseMillis=100', '-XX:MaxGCPauseMillis=400', # default= 200, max = 18446744073709551615 (Maximum value for a variable of type unsigned long) 
         '-Xmx3980m', '-Xmx15920m', '-Xms249m', '-Xms996m', # -Xms default is 498 MB. -Xmx default is 7960 MB (in container with java11, 8gb mem, 2 cpus, ubuntu:22.04)
         '-Xss512k', '-Xss2048k'] # -Xss default is 1024k

CONFLICTS = {
    # Tiered compilation conflicts
    '-XX:TieredStopAtLevel=2': ['-XX:-TieredCompilation', '-XX:-DoEscapeAnalysis'],
    
    # GC type conflicts
    '-XX:+UseParallelGC': ['-XX:+UseSerialGC'],
    '-XX:+UseSerialGC': ['-XX:+UseParallelGC', '-XX:+AggressiveHeap'],
    '-XX:+AggressiveHeap': ['-XX:+UseSerialGC'],
    
    # C1 method data conflicts
    '-XX:-C1UpdateMethodData': ['-XX:-C1ProfileCalls', '-XX:-C1ProfileInlinedCalls', '-XX:-C1ProfileBranches'],
    '-XX:-C1ProfileCalls': ['-XX:-C1UpdateMethodData'],
    '-XX:-C1ProfileInlinedCalls': ['-XX:-C1UpdateMethodData'],
    '-XX:-C1ProfileBranches': ['-XX:-C1UpdateMethodData'],
    
    # New ratio conflicts
    '-XX:NewRatio=1': ['-XX:NewRatio=4'],
    '-XX:NewRatio=4': ['-XX:NewRatio=1'],
    
    # G1 specific conflicts - even though UseG1GC isn't in FLAGS, handle conflicts correctly
    '-XX:G1HeapRegionSize=16m': ['-XX:+UseParallelGC', '-XX:+UseSerialGC', '-XX:G1HeapRegionSize=32m'],
    '-XX:G1HeapRegionSize=32m': ['-XX:+UseParallelGC', '-XX:+UseSerialGC', '-XX:G1HeapRegionSize=16m'],
    '-XX:G1ReservePercent=5': ['-XX:+UseParallelGC', '-XX:+UseSerialGC', '-XX:G1ReservePercent=20'],
    '-XX:G1ReservePercent=20': ['-XX:+UseParallelGC', '-XX:+UseSerialGC', '-XX:G1ReservePercent=5'],
    '-XX:+UseStringDeduplication': ['-XX:+UseParallelGC', '-XX:+UseSerialGC'],
    '-XX:InitiatingHeapOccupancyPercent=22': ['-XX:+UseParallelGC', '-XX:+UseSerialGC', '-XX:InitiatingHeapOccupancyPercent=90'],
    '-XX:InitiatingHeapOccupancyPercent=90': ['-XX:+UseParallelGC', '-XX:+UseSerialGC', '-XX:InitiatingHeapOccupancyPercent=22'],
    
    # GC threads conflicts
    '-XX:ParallelGCThreads=6': ['-XX:ParallelGCThreads=26', '-XX:+UseSerialGC'],
    '-XX:ParallelGCThreads=26': ['-XX:ParallelGCThreads=6', '-XX:+UseSerialGC'],
    
    # Survivor ratio conflicts
    '-XX:SurvivorRatio=4': ['-XX:SurvivorRatio=16'],
    '-XX:SurvivorRatio=16': ['-XX:SurvivorRatio=4'],
    
    # Target survivor ratio conflicts
    '-XX:TargetSurvivorRatio=25': ['-XX:TargetSurvivorRatio=100'],
    '-XX:TargetSurvivorRatio=100': ['-XX:TargetSurvivorRatio=25'],
    
    # Prefetch style conflicts
    '-XX:AllocatePrefetchStyle=2': ['-XX:-UseTLAB', '-XX:AllocatePrefetchStyle=0', '-XX:AllocatePrefetchStyle=3'],
    '-XX:AllocatePrefetchStyle=3': ['-XX:-UseTLAB', '-XX:AllocatePrefetchStyle=0', '-XX:AllocatePrefetchStyle=2'],
    '-XX:AllocatePrefetchStyle=0': ['-XX:-UseTLAB', '-XX:AllocatePrefetchStyle=2', '-XX:AllocatePrefetchStyle=3'],
    
    # Compiler count conflicts
    '-XX:CICompilerCount=6': ['-XX:CICompilerCount=24', '-XX:-TieredCompilation'],
    '-XX:CICompilerCount=24': ['-XX:CICompilerCount=6', '-XX:-TieredCompilation'],
    
    # Heap ratio conflicts
    '-XX:MaxHeapFreeRatio=0': ['-XX:MaxHeapFreeRatio=35', '-XX:MaxHeapFreeRatio=100'],
    '-XX:MaxHeapFreeRatio=35': ['-XX:MaxHeapFreeRatio=0', '-XX:MaxHeapFreeRatio=100'], 
    '-XX:MaxHeapFreeRatio=100': ['-XX:MaxHeapFreeRatio=0', '-XX:MaxHeapFreeRatio=35'],
    
    # Logical min/max heap ratios
    '-XX:MinHeapFreeRatio=0': ['-XX:MinHeapFreeRatio=20', '-XX:MinHeapFreeRatio=80'],
    '-XX:MinHeapFreeRatio=20': ['-XX:MinHeapFreeRatio=0', '-XX:MinHeapFreeRatio=80'],
    '-XX:MinHeapFreeRatio=80': ['-XX:MinHeapFreeRatio=0', '-XX:MinHeapFreeRatio=20', '-XX:MaxHeapFreeRatio=0', '-XX:MaxHeapFreeRatio=35'],
    
    # Escape analysis conflicts
    '-XX:-DoEscapeAnalysis': ['-XX:TieredStopAtLevel=2'],
    
    # Object alignment conflicts
    '-XX:ObjectAlignmentInBytes=16': ['-XX:ObjectAlignmentInBytes=64', '-XX:ObjectAlignmentInBytes=32'],
    '-XX:ObjectAlignmentInBytes=64': ['-XX:ObjectAlignmentInBytes=16', '-XX:ObjectAlignmentInBytes=32'],
    '-XX:ObjectAlignmentInBytes=32': ['-XX:ObjectAlignmentInBytes=64', '-XX:ObjectAlignmentInBytes=16'],
    
    # Prefetch conflicts
    '-XX:AllocatePrefetchStepSize=32': ['-XX:AllocatePrefetchStepSize=128'],
    '-XX:AllocatePrefetchStepSize=128': ['-XX:AllocatePrefetchStepSize=32'],
    '-XX:AllocateInstancePrefetchLines=2': ['-XX:AllocateInstancePrefetchLines=4'],
    '-XX:AllocateInstancePrefetchLines=4': ['-XX:AllocateInstancePrefetchLines=2'],
    '-XX:AllocatePrefetchInstr=0': ['-XX:AllocatePrefetchInstr=1', '-XX:AllocatePrefetchInstr=2'],
    '-XX:AllocatePrefetchInstr=1': ['-XX:AllocatePrefetchInstr=0', '-XX:AllocatePrefetchInstr=2'],
    '-XX:AllocatePrefetchInstr=2': ['-XX:AllocatePrefetchInstr=1', '-XX:AllocatePrefetchInstr=0'],
    '-XX:AllocatePrefetchLines=2': ['-XX:AllocatePrefetchLines=8'],
    '-XX:AllocatePrefetchLines=8': ['-XX:AllocatePrefetchLines=2'],
    
    # Code cache conflicts
    '-XX:ReservedCodeCacheSize=120M': ['-XX:ReservedCodeCacheSize=480M', '-XX:-TieredCompilation'],
    '-XX:ReservedCodeCacheSize=480M': ['-XX:ReservedCodeCacheSize=120M', '-XX:-TieredCompilation'],
    
    # GC pause time conflicts
    '-XX:MaxGCPauseMillis=100': ['-XX:MaxGCPauseMillis=400'],
    '-XX:MaxGCPauseMillis=400': ['-XX:MaxGCPauseMillis=100'],
    
    # Memory size conflicts
    '-Xmx3980m': ['-Xmx15920m'],
    '-Xmx15920m': ['-Xmx3980m'],
    '-Xms249m': ['-Xms996m'],
    '-Xms996m': ['-Xms249m'],
    '-Xss512k': ['-Xss2048k'],
    '-Xss2048k': ['-Xss512k']
}

DEPENDENCIES = {
    # Parallel GC dependencies
    '-XX:ParallelGCThreads=6': ['-XX:+UseParallelGC'],
    '-XX:ParallelGCThreads=26': ['-XX:+UseParallelGC'],
    
    # Heap ratio dependencies
    '-XX:MaxHeapFreeRatio=0': ['-XX:MinHeapFreeRatio=0'],
    '-XX:MaxHeapFreeRatio=35': ['-XX:MinHeapFreeRatio=0', '-XX:MinHeapFreeRatio=20'],
    '-XX:MinHeapFreeRatio=0': ['-XX:MaxHeapFreeRatio=0'],
    '-XX:MinHeapFreeRatio=80': ['-XX:MaxHeapFreeRatio=100'],
    
    # Added dependencies specifically for non-default GCs
    '-XX:-UseAdaptiveSizePolicy': ['-XX:+UseParallelGC'],
    '-XX:+ScavengeBeforeFullGC': ['-XX:+UseParallelGC']
    
    # Note: G1GC-specific flags don't need explicit dependencies
    # since G1GC is the default collector in Java 11
}

def compare_nested_dicts(dict1, dict2):
    return isinstance(dict1, dict) and isinstance(dict2, dict) and dict1 == dict2

def write_to_json(runtimes, results_nums, test_method_to_test_time_lst, output_path):
    """Save the results of the runs of one combination, one list entry per run."""
    json_dict = {
        'overall_stats': {
            "min": round(np.min(runtimes), 3),
            "max": round(np.max(runtimes), 3),
            "mean": round(np.mean(runtimes), 3),
            "median": round(np.median(runtimes), 3),
            "sd": round(np.std(runtimes), 3),
            "variance": round(np.var(runtimes), 3),
        },
        'times': runtimes,
        'test_methods_times': test_method_to_test_time_lst,
        'results_tuples': results_nums,
    }
    with open(output_path, 'w') as output_file:
        json.dump(json_dict, output_file)

def simplify_method_name(method_name): # Handle Memory addresses and Parameterized identifiers
    """
        - ex: testFoo@6e2c634b -> testFoo@<normalized>
        - ex: testBar[5] -> testBar[5]
        - ex: testBar[5][6] -> testBar[5]
    """
    # Step 1: Remove memory address in the format @<hex> from the method name
    # Regex pattern: match '@' followed by a sequence of alphanumeric characters (the memory address)
    simplified_method_name = re.sub(r'@[\da-fA-F]+', '@<normalized>', method_name)
    
    # Step 2: Use regex to find the first number in brackets and truncate the rest
    match = re.search(r'\[(\d+)', simplified_method_name)
    if match:
        # Extract everything up to and including the first number in the brackets
        truncated_method_name = simplified_method_name[:match.end()] + "]"
        return truncated_method_name
    
    # Return the modified method name after both simplifications
    return simplified_method_name

def get_all_individual_tests_runtime(surefire_reports_dir):
    summary = {'Tests run': 0, 'Failures': 0, 'Errors': 0, 'Skipped': 0}
    test_method_to_test_time = {}

    for root_dir, _, files in os.walk(surefire_reports_dir):
        for file in files:
            if file.endswith('.xml'):
                xml_file = os.path.join(root_dir, file)
                tree = ET.parse(xml_file)
                root = tree.getroot()
                for testcase in root.findall('testcase'):
                    summary['Tests run'] += 1
                    class_name = testcase.attrib['classname']
                    method_name = testcase.attrib['name']
                    method_name = simplify_method_name(method_name)
                    test_method = f"{class_name}#{method_name}"
                    test_time = float(testcase.get('time'))

                    result = 'pass'
                    if testcase.find('skipped') is not None:
                        summary['Skipped'] += 1
                        result = 'skipped'
                    elif testcase.find('error') is not None:
                        summary['Errors'] += 1
                        result = 'error'
                    elif testcase.find('failure') is not None:
                        summary['Failures'] += 1
                        result = 'failure'

                    test_method_to_test_time[test_method] = {'result': result, 'time': test_time}

    return summary, test_method_to_test_time

def validate_combination(combination):
    """Make a combination valid: add the flags that its flags depend on, and remove conflicting flags.

    Returns the flags of the valid combination, sorted.
    """
    combination_set = set(combination)

    # A dependency is satisfied if any one of the flags it lists is present; otherwise add the first
    for flag in sorted(combination_set):
        required_deps = DEPENDENCIES.get(flag, [])
        if required_deps and not any(dep in combination_set for dep in required_deps):
            combination_set.add(required_deps[0])

    # Resolve one conflict at a time, until none is left
    while True:
        flags = sorted(combination_set)
        conflict = next(((left, right) for left in flags for right in flags
                         if left != right and right in CONFLICTS.get(left, [])), None)
        if conflict is None:
            break

        # Of the two flags, remove the one that fewer flags depend on, together with those flags
        left, right = conflict
        left_dependents = {flag for flag in combination_set if left in DEPENDENCIES.get(flag, [])}
        right_dependents = {flag for flag in combination_set if right in DEPENDENCIES.get(flag, [])}
        if len(left_dependents) > len(right_dependents):
            combination_set -= {right} | right_dependents
        else:
            combination_set -= {left} | left_dependents

    return sorted(combination_set)

def get_random_combinations(flag_space, num_combinations, max_len, seen):
    """Sample valid combinations at random that are not in seen, as sorted, space-separated strings."""
    combs_ready = []
    max_combination_size = min(max_len, len(flag_space))

    while len(combs_ready) < num_combinations:
        combination_size = random.randint(1, max_combination_size)
        combination = random.sample(flag_space, combination_size)
        combination_str = ' '.join(validate_combination(combination))

        if combination_str not in combs_ready and combination_str not in seen:
            combs_ready.append(combination_str)

    return combs_ready

def get_module_time(directory):
    """Sum the time of all test suites in a surefire-reports directory."""
    overall_testsuite_time = 0.0
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.xml'):
                testsuite = ET.parse(os.path.join(root, file)).getroot()
                overall_testsuite_time += float(testsuite.get('time').replace(',', ''))
    return round(overall_testsuite_time, 3)

def get_sorted_combinations_results(ALL_COMBS_RESULTS, combs):
    """Compare the runtimes of combinations with those of the default configuration.

    Returns (combination, (mean runtime, % reduction from the default, Mann-Whitney U p-value))
    tuples, sorted from the largest reduction to the smallest.
    """
    default_times = ALL_COMBS_RESULTS['default']
    default_avg = round(float(np.mean(default_times)), 3)

    info = {}
    for comb in combs:
        if comb in ALL_COMBS_RESULTS:
            runtimes = ALL_COMBS_RESULTS[comb]
            average = round(float(np.mean(runtimes)), 3)
            reduction = round(100 * (default_avg - average) / default_avg, 3)
            p_val = round(stats.mannwhitneyu(runtimes, default_times)[1], 3)
            info[comb] = (average, reduction, p_val)

    return sorted(info.items(), key=lambda x: x[1][1], reverse=True)

def create_table_all(file_dir, info_sorted, file_name="all_combinations_table.txt"):
    """Write the output of get_sorted_combinations_results as a table."""
    col_names = ['Configuration Name', 'Runtime (sec)', 'Percent Diff Default', 'P-value w.r.t default']
    data = [[comb, average, reduction, p_val] for comb, (average, reduction, p_val) in info_sorted]
    with open(os.path.join(file_dir, file_name), 'w') as f:
        f.write(tabulate(data, headers=col_names, tablefmt="github"))

def check_argLine_options(project_path, options):
    """Check whether an argLine in the project's pom.xml contains any of the given options."""
    pom_file = os.path.join(project_path, 'pom.xml')
    pre = '{http://maven.apache.org/POM/4.0.0}'
    root = ET.parse(pom_file).getroot()
    for argLine in root.iter(pre + 'argLine'):
        if argLine.text is not None and any(option in argLine.text for option in options):
            return True
    return False
