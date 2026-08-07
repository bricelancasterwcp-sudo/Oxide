fn main() {
    let mut n: i64 = 27;
    let mut steps = 0;
    while n > 1 {
        if n % 2 == 0 {
            n /= 2;
        } else {
            n = 3 * n + 1;
        }
        steps += 1;
    }
    println!("{}", steps);
}
