package parser;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Optional;


/** Finds the directories that hold a JFR recording, and the GC log next to it if there is one. */
public class FileFinder {

    public static class DataFiles {
        public String path;  // of the directory, relative to the input root

        public File jfrFile;
        public Optional<File> gcLogFile;

        public DataFiles(String path, File jfrFile, Optional<File> gcLogFile) {
            this.path = path;
            this.jfrFile = jfrFile;
            this.gcLogFile = gcLogFile;
        }
    }

    public static boolean doesContainDataFiles(File dir) {
        for (File f : dir.listFiles()) {
            if (f.isFile() && f.getName().endsWith(".jfr"))
                return true;
        }
        return false;
    }

    public static DataFiles extractDataFiles(File root, File dir) throws IOException {
        Optional<File> jfrFile = Arrays.stream(dir.listFiles())
            .filter((f) -> f.isFile() && f.getName().endsWith(".jfr"))
            .findFirst();
        
        if (!jfrFile.isPresent()) return null;
        
        Optional<File> gcLogFile = Optional.ofNullable(
            Path.of(dir.getPath(), "gc.log").toFile().exists() ?
                Path.of(dir.getPath(), "gc.log").toFile() : null
        );

        return new DataFiles(
            root.toPath().relativize(dir.toPath()).toString(),
            jfrFile.get(),
            gcLogFile
        );
    }

    private static ArrayList<DataFiles> getAllDataFiles(File root, File curr, ArrayList<DataFiles> files) throws IOException {
        for (File f : curr.listFiles()) {
            if (f.isDirectory()) {
                if (doesContainDataFiles(f)) files.add(extractDataFiles(root, f));
                else getAllDataFiles(root, f, files);
            }
        }
        return files;
    }

    public static ArrayList<DataFiles> getAllDataFiles(File root) throws IOException {
        ArrayList<DataFiles> dfs = new ArrayList<>();
        return getAllDataFiles(root, root, dfs);
    }

}