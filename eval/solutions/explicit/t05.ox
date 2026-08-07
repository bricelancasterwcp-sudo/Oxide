fn main() {
    let a = 1
    let b = 1
    let i = 2
    while i < 20 {
        let c = a + b
        a = b
        b = c
        i = i + 1
    }
    print(b)
}
