package parser;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.stream.Collectors;

import jdk.jfr.consumer.*;


public class AggregateJFRData {

    public static ArrayList<RecordedEvent> getSortedAndFilteredEvents(HashMap<String, ArrayList<RecordedEvent>> eventData, String key) {
        return eventData.get(key).stream()
            .filter((e) -> e.hasField("startTime") && e.getInstant("startTime") != null)
            .sorted((e1, e2) -> e1.getInstant("startTime").compareTo(e2.getInstant("startTime")))
            .collect(Collectors.toCollection(ArrayList::new));
    }

    /** Reads a recording and groups its events by event type. */
    public static JFRSummary getEvents(RecordingFile file) throws IOException {
        JFRSummary summary = new JFRSummary();

        while (file.hasMoreEvents()) {
            RecordedEvent event = file.readEvent();

            String eventName = event.getEventType().getName();
            if (!summary.events.containsKey(eventName))
                summary.events.put(eventName, new ArrayList<RecordedEvent>());
            summary.events.get(eventName).add(event);
        }

        String[] eventsToSort = {
            "CPULoad", "ClassLoad", "Compilation", 
            "FileRead", "FileWrite", "SocketRead", "SocketWrite", 
            "GCHeapSummary", "GarbageCollection",
            "ExecutionSample"
        };
        for (String event : eventsToSort) {
            if (summary.events.containsKey("jdk." + event))
                summary.events.put("jdk." + event, getSortedAndFilteredEvents(summary.events, "jdk." + event));
        }

        return summary;
    }

}