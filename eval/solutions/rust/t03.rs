fn main() {
    let mut a = 252;
    let mut b = 105;
    while b > 0 {
        let t = a % b;
        a = b;
        b = t;
    }
    println!("{}", a);
}
