package parser;

import jdk.jfr.consumer.RecordingFile;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;


/**
 * Extracts the runtime metrics from every JFR recording under a directory.
 * Usage: JFRParser &lt;input dir&gt; &lt;output dir&gt;
 */
public class JFRParser {

    private static int maxCores = Runtime.getRuntime().availableProcessors();

    public static void main(String[] args) throws IOException, InterruptedException {
        File rootDir = new File(args[0]);
        ArrayList<FileFinder.DataFiles> inputData = FileFinder.getAllDataFiles(rootDir);

        // The recordings are split evenly over the threads
        int numThreads = (maxCores - 1) * 2 + 1;
        Thread[] threads = new Thread[numThreads];

        int baseWorkloadSize = inputData.size() / numThreads;
        int modWorkloadSize = inputData.size() % numThreads;

        for (int i = 0; i < threads.length; i++) {
            int workloadSize = baseWorkloadSize + (i < modWorkloadSize ? 1 : 0);
            int workloadStart = i * baseWorkloadSize + (i < modWorkloadSize ? i : modWorkloadSize);

            threads[i] = new Thread(() -> {
                for (int j = workloadStart; j < workloadSize+workloadStart; j++) {
                    FileFinder.DataFiles dfs = inputData.get(j);
                    System.out.println("Begin processing test suite for " + dfs.path);
                    try {
                        AggregateTestSuite.writeTestSuiteSummary(
                            new RecordingFile(dfs.jfrFile.toPath()),
                            dfs.gcLogFile,
                            Path.of(args[1], dfs.path).toString()
                        );
                    } catch (Exception e) {
                        System.out.println(e);
                        System.out.println("Unable to generate output for " + dfs.path);
                    }
                    System.out.println("Done processing test suite for " + dfs.path);
                }
            });

            threads[i].start();
        }

        for (Thread t : threads) t.join();
    }
}
