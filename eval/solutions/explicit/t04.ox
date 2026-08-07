fn main() {
    let count = 0
    for n in range(2, 100) {
        let is_prime = true
        let d = 2
        while d * d <= n {
            if n % d == 0 {
                is_prime = false
            }
            d = d + 1
        }
        if is_prime {
            count = count + 1
        }
    }
    print(count)
}
