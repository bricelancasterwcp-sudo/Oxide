fn main() {
    let a = 252
    let b = 105
    while b > 0 {
        let t = a % b
        a = b
        b = t
    }
    print(a)
}
