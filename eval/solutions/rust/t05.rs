fn main() {
    let mut a: u64 = 1;
    let mut b: u64 = 1;
    for _ in 2..20 {
        let c = a + b;
        a = b;
        b = c;
    }
    println!("{}", b);
}
