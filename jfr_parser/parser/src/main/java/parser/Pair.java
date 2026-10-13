package parser;


public class Pair<T, U> {

    private T fst;
    private U snd;

    public Pair(T fst, U snd) {
        this.fst = fst;
        this.snd = snd;
    }

    public T fst() { return fst; }
    public U snd() { return snd; }

}