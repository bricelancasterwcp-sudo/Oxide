fn main() {
    for n in 1..1000 {
        let mut s = 0;
        for d in 1..n {
            if n % d == 0 {
                s += d;
            }
        }
        if s == n {
            println!("{}", n);
        }
    }
}
