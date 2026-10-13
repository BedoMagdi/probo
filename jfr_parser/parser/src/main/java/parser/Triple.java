package parser;


public class Triple<T, U, R> {

    private T fst;
    private U snd;
    private R thd;

    public Triple(T fst, U snd, R thd) {
        this.fst = fst;
        this.snd = snd;
        this.thd = thd;
    }

    public T fst() { return fst; }
    public U snd() { return snd; }
    public R thd() { return thd; }

}