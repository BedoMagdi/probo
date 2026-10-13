package parser;

import jdk.jfr.consumer.*;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Scanner;
import java.util.Set;
import java.util.stream.Collectors;

import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.function.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;


/**
 * Computes the runtime metrics of one test-suite run from its JFR recording and writes them to
 * by_test_suite.csv, one "name,value" row per metric. A metric that cannot be computed, because
 * the recording has no events of the type it needs, has the value "-".
 */
public class AggregateTestSuite extends AggregateJFRData {

    private static String metricRow(String name, double value) {
        return name + "," + (value < 0 ? "-" : String.valueOf(value));
    }

    private static double timeWeightedAvgForKey(JFRSummary summary, String key, Function<RecordedEvent, Double> extractVal) {
        if (!summary.events.containsKey(key) || summary.events.get(key).isEmpty()) return -1;
        
        double sum = 0;
        double weightSum = 0;
        Instant prev = null;

        for (int i = 0; i < summary.events.get(key).size(); i++) {
            Instant eventStart = summary.events.get(key).get(i).getStartTime();

            double prevDist = prev == null ? 0 : Duration.between(prev, eventStart).toMillis();
            double nextDist  = i == summary.events.get(key).size() - 1 ? 0 :
                Duration.between(
                    eventStart, 
                    summary.events.get(key).get(i+1).getStartTime()
                ).toMillis();
            double weight = prevDist / 2 + nextDist / 2 + 1;

            weightSum += weight;
            sum += weight * extractVal.apply(summary.events.get(key).get(i));

            prev = eventStart;
        }

        return sum / weightSum;
    }

    private static double getTestSuiteRuntimeSecs(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.JVMInformation")
            || !summary.events.containsKey("jdk.Shutdown")
            || summary.events.get("jdk.JVMInformation").isEmpty()
            || summary.events.get("jdk.Shutdown").isEmpty()) {
            return -1;   
        }

        Instant start = summary.events.get("jdk.JVMInformation")
                                    .get(0)
                                    .getInstant("jvmStartTime");

        List<RecordedEvent> shutdownEvents = summary.events.get("jdk.Shutdown");
        Instant end = shutdownEvents.get(shutdownEvents.size() - 1)
                                    .getInstant("startTime");

        long duration = Duration.between(start, end).toMillis();

        return duration < 10000 ? (duration / 1000.0) : ((double) (duration / 1000));
    }

    private static long countGCs(JFRSummary summary) {
        return summary.events.get("jdk.GarbageCollection").stream()
            .mapToInt((e) -> e.getInt("gcId"))
            .distinct()
            .count();
    }

    private static double calculateGCFrequency(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0 || !summary.events.containsKey("jdk.GarbageCollection")) {
            return -1;
        }

        return countGCs(summary) / runtimeSecs;
    }

    private static double calculateGCPauseVariance(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.GCPhasePause")) {
            return -1;
        }
        
        List<Double> pauseTimes = summary.events.get("jdk.GCPhasePause").stream()
            .filter(e -> e.getString("name").equals("GC Pause"))
            .map(e -> (double) e.getDuration().toMillis())
            .collect(Collectors.toList());
        
        if (pauseTimes.size() < 2) {
            return 0;
        }
        
        double mean = pauseTimes.stream().mapToDouble(Double::doubleValue).average().orElse(0);
        double variance = pauseTimes.stream()
            .mapToDouble(time -> Math.pow(time - mean, 2))
            .average().orElse(0);
        
        return variance;
    }

    private static double calculateAllocationRate(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0) {
            return -1;
        }
        
        double totalAllocated = 0;
        
        if (summary.events.containsKey("jdk.ObjectAllocationInNewTLAB")) {
            totalAllocated += summary.events.get("jdk.ObjectAllocationInNewTLAB").stream()
                .mapToDouble(e -> e.getLong("allocationSize"))
                .sum();
        }
        
        if (summary.events.containsKey("jdk.ObjectAllocationOutsideTLAB")) {
            totalAllocated += summary.events.get("jdk.ObjectAllocationOutsideTLAB").stream()
                .mapToDouble(e -> e.getLong("allocationSize"))
                .sum();
        }
        
        return totalAllocated / runtimeSecs / 1000; // Convert to KB/s
    }

    private static double calculateC1CompilationRate(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0 || !summary.events.containsKey("jdk.Compilation")) {
            return -1;
        }
        
        long c1Compilations = summary.events.get("jdk.Compilation").stream()
            .filter(e -> e.getInt("compileLevel") >= 1 && e.getInt("compileLevel") <= 3)
            .count();
        
        return c1Compilations / runtimeSecs;
    }

    private static double calculateC2CompilationRate(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0 || !summary.events.containsKey("jdk.Compilation")) {
            return -1;
        }
        
        long c2Compilations = summary.events.get("jdk.Compilation").stream()
            .filter(e -> e.getInt("compileLevel") == 4)
            .count();
        
        return c2Compilations / runtimeSecs;
    }

    private static double calculateTotalCompilationRate(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0 || !summary.events.containsKey("jdk.Compilation")) {
            return -1;
        }
        
        long totalCompilations = summary.events.get("jdk.Compilation").size();
        return totalCompilations / runtimeSecs;
    }

    private static double calculateC1C2Ratio(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0 || !summary.events.containsKey("jdk.Compilation")) {
            return -1;
        }
        
        long c1Compilations = summary.events.get("jdk.Compilation").stream()
            .filter(e -> e.getInt("compileLevel") >= 1 && e.getInt("compileLevel") <= 3)
            .count();
        
        long c2Compilations = summary.events.get("jdk.Compilation").stream()
            .filter(e -> e.getInt("compileLevel") == 4)
            .count();
        
        // Add small epsilon to avoid division by zero when C2 = 0
        double epsilon = 1e-6;
        double c2WithEpsilon = c2Compilations + epsilon;
        
        return (double) c1Compilations / c2WithEpsilon;
    }

    private static double calculateDeoptimizationRate(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.Deoptimization") || !summary.events.containsKey("jdk.Compilation")) {
            return -1;
        }

        long totalDeoptimizations = summary.events.get("jdk.Deoptimization").size();
        long totalCompilations = summary.events.get("jdk.Compilation").size();

        return totalCompilations == 0 ? 0 : (double) totalDeoptimizations / totalCompilations;
    }

    private static double calculateTLABEfficiency(JFRSummary summary) {
        long tlabAllocs = 0;
        long totalAllocs = 0;
        
        if (summary.events.containsKey("jdk.ObjectAllocationInNewTLAB")) {
            tlabAllocs = summary.events.get("jdk.ObjectAllocationInNewTLAB").size();
            totalAllocs += tlabAllocs;
        }
        
        if (summary.events.containsKey("jdk.ObjectAllocationOutsideTLAB")) {
            totalAllocs += summary.events.get("jdk.ObjectAllocationOutsideTLAB").size();
        }
        
        if (totalAllocs == 0) {
            return -1;
        }
        
        return (double) tlabAllocs / totalAllocs;
    }

    private static double calculateCompilationSuccessRate(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.Compilation")) return -1;
        
        int totalCompilations = summary.events.get("jdk.Compilation").size();
        int failedCompilations = summary.events.containsKey("jdk.CompilationFailure") ? 
            summary.events.get("jdk.CompilationFailure").size() : 0;
        
        return totalCompilations == 0 ? 0 : (double)(totalCompilations - failedCompilations) / totalCompilations;
    }

    private static double calculateHotMethodRecompilationRate(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.Compilation")) return -1;
        
        // Count unique methods and total compilations
        Set<String> uniqueMethods = new HashSet<>();
        int totalCompilations = 0;
        
        for (RecordedEvent event : summary.events.get("jdk.Compilation")) {
            try {
                // Get the method as a RecordedObject, then extract its string representation
                if (event.hasField("method")) {
                    Object methodObj = event.getValue("method");
                    if (methodObj != null) {
                        String methodName = methodObj.toString();
                        uniqueMethods.add(methodName);
                        totalCompilations++;
                    }
                }
            } catch (Exception e) {
                // Skip this event if we can't extract method info
                totalCompilations++;
                continue;
            }
        }
        
        return uniqueMethods.size() == 0 ? 0 : (double)totalCompilations / uniqueMethods.size();
    }

    private static double calculateCodeCachePressure(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.CodeCacheStatistics")) return -1;
        
        // Find the maximum code cache usage percentage
        double maxUsage = 0;
        for (RecordedEvent event : summary.events.get("jdk.CodeCacheStatistics")) {
            try {
                long blobCount = event.getLong("blobCount");
                long nmethodCount = event.getLong("nmethodCount");
                long adaptorCount = event.getLong("adaptorCount");
                long unallocatedCapacity = event.getLong("unallocatedCapacity");
                long fullCount = event.getLong("fullCount");
                
                // Approximate usage based on available fields
                double usage = fullCount > 0 ? 0.9 : (double)(blobCount + nmethodCount + adaptorCount) / 10000.0;
                maxUsage = Math.max(maxUsage, Math.min(usage, 1.0));
            } catch (Exception e) {
                // If fields are missing, use a conservative estimate
                continue;
            }
        }
        return maxUsage;
    }

    private static double calculateGCTimingRegularity(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.GarbageCollection") || 
            summary.events.get("jdk.GarbageCollection").size() < 2) return -1;
        
        List<RecordedEvent> gcEvents = summary.events.get("jdk.GarbageCollection");
        List<Double> intervals = new ArrayList<>();
        
        for (int i = 1; i < gcEvents.size(); i++) {
            long interval = Duration.between(
                gcEvents.get(i-1).getStartTime(), 
                gcEvents.get(i).getStartTime()
            ).toMillis();
            intervals.add((double)interval);
        }
        
        // Calculate coefficient of variation (std/mean)
        double mean = intervals.stream().mapToDouble(Double::doubleValue).average().orElse(0);
        if (mean == 0) return 0;
        
        double variance = intervals.stream()
            .mapToDouble(x -> Math.pow(x - mean, 2))
            .average().orElse(0);
        double stdDev = Math.sqrt(variance);
        
        return stdDev / mean;  // Lower = more regular
    }

    private static double calculateCompilationBurstIntensity(JFRSummary summary) {
        if (!summary.events.containsKey("jdk.Compilation")) return -1;
        
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 5) return -1;  // Need at least 5 seconds
        
        // Divide runtime into 1-second windows
        int windows = (int)Math.floor(runtimeSecs);
        int[] compilationsPerWindow = new int[windows];
        
        if (!summary.events.containsKey("jdk.JVMInformation") || 
            summary.events.get("jdk.JVMInformation").isEmpty()) return -1;
            
        Instant startTime = summary.events.get("jdk.JVMInformation").get(0).getInstant("jvmStartTime");
        
        for (RecordedEvent event : summary.events.get("jdk.Compilation")) {
            long secondsFromStart = Duration.between(startTime, event.getStartTime()).getSeconds();
            if (secondsFromStart >= 0 && secondsFromStart < windows) {
                compilationsPerWindow[(int)secondsFromStart]++;
            }
        }
        
        // Calculate coefficient of variation for window counts
        double mean = Arrays.stream(compilationsPerWindow).average().orElse(0);
        if (mean == 0) return 0;
        
        double variance = Arrays.stream(compilationsPerWindow)
            .mapToDouble(x -> Math.pow(x - mean, 2))
            .average().orElse(0);
        double stdDev = Math.sqrt(variance);
        
        return stdDev / mean;  // Higher = more bursty
    }

    private static double calculateSafepointFrequency(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0 || !summary.events.containsKey("jdk.SafepointBegin")) return -1;
        
        return summary.events.get("jdk.SafepointBegin").size() / runtimeSecs;
    }

    private static double calculateThreadContentionRate(JFRSummary summary) {
        double runtimeSecs = getTestSuiteRuntimeSecs(summary);
        if (runtimeSecs <= 0) return -1;
        
        int contentionEvents = 0;
        if (summary.events.containsKey("jdk.JavaMonitorEnter")) {
            contentionEvents += summary.events.get("jdk.JavaMonitorEnter").size();
        }
        if (summary.events.containsKey("jdk.ThreadPark")) {
            contentionEvents += summary.events.get("jdk.ThreadPark").size();
        }
        
        return contentionEvents / runtimeSecs;
    }

    private static DateTimeFormatter formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSZ");
    private static String gcDurationRe = "\\[([^\\]]*)\\] GC\\(([0-9]+)\\) (.*) ([0-9|\\.]+)ms";
    private static Pattern gcDurationPattern = Pattern.compile(gcDurationRe, Pattern.DOTALL);
    private static String gcRe = "\\[([^\\]]*)\\] GC\\(([0-9]+)\\) (.*)";
    private static Pattern gcPattern = Pattern.compile(gcRe, Pattern.DOTALL);

    private static Pair<Integer, Long> parseGCLog(File gcLogFile) throws FileNotFoundException {
        ArrayList<Integer> seenIds = new ArrayList<>();
        long totalPauseTime = 0;

        Scanner scanner = new Scanner(gcLogFile);
        while (scanner.hasNextLine()) {
            String unparsedGcLine = scanner.nextLine();
            Matcher matcher = gcDurationPattern.matcher(unparsedGcLine);

            Instant gcStartTime = null;
            int gcId = 0;
            Duration gcPauseLen = Duration.ofMillis(0);

            if (matcher.matches()) {
                gcId = Integer.parseInt(matcher.group(2));
                gcStartTime = ZonedDateTime.parse(matcher.group(1), formatter).toInstant();
                if (!matcher.group(3).toLowerCase().contains("concurrent"))
                    gcPauseLen = Duration.ofMillis((long)(Double.parseDouble(matcher.group(4))));
            } else {
                matcher = gcPattern.matcher(unparsedGcLine);
                if (matcher.matches()) {
                    gcId = Integer.parseInt(matcher.group(2));
                    gcStartTime = ZonedDateTime.parse(matcher.group(1), formatter).toInstant();
                }
            }

            if (gcStartTime != null) {
                if (!seenIds.contains(gcId)) seenIds.add(gcId);
                totalPauseTime += gcPauseLen.toMillis();
            }
        }
        scanner.close();
        
        return new Pair<>(seenIds.size(), totalPauseTime);
    }

    public static void writeTestSuiteSummary(RecordingFile file, Optional<File> gcLogFile, String dest) throws IOException, FileNotFoundException {
        JFRSummary summary = getEvents(file);

        List<Triple<String, String, Function<RecordedEvent, Double>>> statsToAvg = new ArrayList<>(Arrays.asList(
            new Triple<>("Mean User CPU %", "jdk.CPULoad", (e) -> (double) e.getFloat("jvmUser") * 100),
            new Triple<>("Mean System CPU %", "jdk.CPULoad", (e) -> (double) e.getFloat("jvmSystem") * 100),
            new Triple<>("Mean Committed Heap (KB)", "jdk.GCHeapSummary", (e) -> (double) (e.getLong("heapSpace.committedSize") / 1000)),
            new Triple<>("Mean Used Heap (KB)", "jdk.GCHeapSummary", (e) -> (double) (e.getLong("heapUsed") / 1000)),
            new Triple<>("Mean Used Heap Ratio", "jdk.GCHeapSummary", (e) ->  (double) (e.getLong("heapUsed") / 1000) / (double) (e.getLong("heapSpace.committedSize") / 1000)),
            new Triple<>("Mean Active Threads", "jdk.JavaThreadStatistics", (e) -> (double) e.getLong("activeCount")),
            new Triple<>("Mean Daemon Threads", "jdk.JavaThreadStatistics", (e) -> (double) e.getLong("daemonCount")),
            new Triple<>("Mean Thread Sleep (ms)", "jdk.ThreadSleep", (e) -> (double) (Duration.between(e.getStartTime(), e.getEndTime()).toMillis()))
        ));

        List<Triple<String, String, Function<RecordedEvent, Double>>> statsToSum = new ArrayList<>(Arrays.asList(
            new Triple<>("Total Thread Sleep (ms)", "jdk.ThreadSleep", (e) -> (double) (Duration.between(e.getStartTime(), e.getEndTime()).toMillis())),
            new Triple<>("Classes Loaded", "jdk.ClassLoad", (e) -> 1.0),
            new Triple<>("Classloading Duration (ms)", "jdk.ClassLoad", (e) -> (double) e.getDuration().toMillis()),
            new Triple<>("Methods compiled", "jdk.Compilation", (e) -> 1.0),
            new Triple<>("Compilation Duration (ms)", "jdk.Compilation", (e) -> (double) e.getDuration().toMillis()),
            new Triple<>("File Read Amount (KB)", "jdk.FileRead", (e) -> 
                e.getString("eventThread.osName").equals("main") ? (double) e.getLong("bytesRead") / 1000 : 0),
            new Triple<>("File Write Amount (KB)", "jdk.FileWrite", (e) ->
                e.getString("eventThread.osName").equals("main") ? (double) e.getLong("bytesWritten") / 1000 : 0),
            new Triple<>("Socket Read Amount (KB)", "jdk.SocketRead", (e) -> 
                e.getString("eventThread.osName").equals("main") ? (double) e.getLong("bytesRead") / 1000 : 0),
            new Triple<>("Socket Write Amount (KB)", "jdk.SocketWrite", (e) -> 
                e.getString("eventThread.osName").equals("main") ? (double) e.getLong("bytesWritten") / 1000 : 0)
        ));

        if (!gcLogFile.isPresent()) {
            statsToSum.add(new Triple<>("GC Pause Time (ms)", "jdk.GCPhasePause", (e) -> 
                e.getString("name").equals("GC Pause") ? (double) e.getDuration().toMillis() : 0));
        }

        List<Triple<String, String, Function<RecordedEvent, Double>>> statsToMinMax = new ArrayList<>(Arrays.asList(
            new Triple<>("Committed Heap (KB)", "jdk.GCHeapSummary", (e) -> (double) (e.getLong("heapSpace.committedSize") / 1000)),
            new Triple<>("Used Heap (KB)", "jdk.GCHeapSummary", (e) -> (double) (e.getLong("heapUsed") / 1000)),
            new Triple<>("Used Heap Ratio", "jdk.GCHeapSummary", (e) ->  (double) (e.getLong("heapUsed") / 1000) / (double) (e.getLong("heapSpace.committedSize") / 1000)),
            new Triple<>("Active Threads", "jdk.JavaThreadStatistics", (e) -> (double) e.getLong("activeCount")),
            new Triple<>("Daemon Threads", "jdk.JavaThreadStatistics", (e) -> (double) e.getLong("daemonCount"))
        ));

        String minMaxStats = statsToMinMax.stream()
            .map((s) -> {
                if (!summary.events.containsKey(s.snd())) return s.fst() + ",-";
                double max = summary.events.get(s.snd()).stream()
                    .mapToDouble((e) -> s.thd().apply(e))
                    .max()
                    .getAsDouble();
                double min = summary.events.get(s.snd()).stream()
                    .mapToDouble((e) -> s.thd().apply(e))
                    .min()
                    .getAsDouble();
                return "Max " + s.fst() + "," + max + "\nMin " + s.fst() + "," + min;
            })
            .collect(Collectors.joining("\n"));

        String avgStats = statsToAvg.stream()
            .map((s) -> metricRow(s.fst(), timeWeightedAvgForKey(summary, s.snd(), s.thd())))
            .collect(Collectors.joining("\n"));
        
        String sumStats = statsToSum.stream()
            .map((s) -> {
                if (!summary.events.containsKey(s.snd())) return s.fst() + ",-";
                return s.fst() + "," + summary.events.get(s.snd()).stream()
                    .reduce(
                        0.0, 
                        (sum, e) -> sum + s.thd().apply(e), 
                        (sum1, sum2) -> sum1 + sum2
                    )
                    .toString();
            })
            .collect(Collectors.joining("\n"));
        
        // The GC log is the preferred source of the GC count and pause time; without one they come from the JFR events
        String totalGcStat = "Total GC Count,";
        if (gcLogFile.isPresent()) {
            Pair<Integer, Long> gcStats = parseGCLog(gcLogFile.get());
            totalGcStat += gcStats.fst();
            totalGcStat += "\nGC Pause Time (ms)," + gcStats.snd();
        } else {
            totalGcStat += countGCs(summary);
        }

        String durationStat = metricRow("Duration (s)", getTestSuiteRuntimeSecs(summary));

        String derivedStats = String.join("\n",
            metricRow("GC Frequency (events/s)", calculateGCFrequency(summary)),
            metricRow("GC Pause Variance (ms²)", calculateGCPauseVariance(summary)),
            metricRow("GC Timing Regularity", calculateGCTimingRegularity(summary)),
            metricRow("Allocation Rate (KB/s)", calculateAllocationRate(summary)),
            metricRow("C1 Compilation Rate (events/s)", calculateC1CompilationRate(summary)),
            metricRow("C2 Compilation Rate (events/s)", calculateC2CompilationRate(summary)),
            metricRow("Total Compilation Rate (events/s)", calculateTotalCompilationRate(summary)),
            metricRow("Compilation Success Rate", calculateCompilationSuccessRate(summary)),
            metricRow("Hot Method Recompilation Rate", calculateHotMethodRecompilationRate(summary)),
            metricRow("Compilation Burst Intensity", calculateCompilationBurstIntensity(summary)),
            metricRow("Code Cache Pressure", calculateCodeCachePressure(summary)),
            metricRow("C1/C2 Ratio", calculateC1C2Ratio(summary)),
            metricRow("Deoptimization Rate", calculateDeoptimizationRate(summary)),
            metricRow("TLAB Efficiency", calculateTLABEfficiency(summary)),
            metricRow("Safepoint Frequency (events/s)", calculateSafepointFrequency(summary)),
            metricRow("Thread Contention Rate (events/s)", calculateThreadContentionRate(summary))
        );

        String allMetrics = durationStat + "\n" + minMaxStats + "\n" + avgStats + "\n" + sumStats + "\n" + totalGcStat + "\n" + derivedStats;
        Utils.writeResults(dest, "by_test_suite.csv", allMetrics);
    }
}