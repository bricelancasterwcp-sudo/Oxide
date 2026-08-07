fn main() {
    let mut count = 0;
    for n in 2..100 {
        let mut is_prime = true;
        let mut d = 2;
        while d * d <= n {
            if n % d == 0 {
                is_prime = false;
            }
            d += 1;
        }
        if is_prime {
            count += 1;
        }
    }
    println!("{}", count);
}
