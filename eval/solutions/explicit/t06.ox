fn main() {
    for n in range(1, 1000) {
        let s = 0
        for d in range(1, n) {
            if n % d == 0 {
                s = s + d
            }
        }
        if s == n {
            print(n)
        }
    }
}
