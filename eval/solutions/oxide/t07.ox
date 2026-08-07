fn main() {
    let v = vec()
    for i in range(1, 9) {
        v = push(v, i * 3)
    }
    let s = 0
    for x in v {
        s = s + x
    }
    print(s)
}
