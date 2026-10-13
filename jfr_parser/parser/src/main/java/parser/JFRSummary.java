package parser;

import jdk.jfr.consumer.RecordedEvent;

import java.util.HashMap;
import java.util.ArrayList;


/** The events of one recording, by event type (for example "jdk.GarbageCollection"). */
public class JFRSummary {
    HashMap<String, ArrayList<RecordedEvent>> events = new HashMap<>();
}