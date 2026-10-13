package parser;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.nio.file.Paths;


public class Utils {

    public static void writeResults(String destDir, String fileName, String data) {
        (new File(destDir)).mkdirs();
        try (BufferedWriter writer = new BufferedWriter(new FileWriter(Paths.get(destDir, fileName).toString()))) {
            writer.write(data);
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}