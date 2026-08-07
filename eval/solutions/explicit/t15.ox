fn main() {
    let v = push(push(push(vec(), "12"), "x"), "30")
    let sum = 0
    let failed = 0
    for s in &v {
        match parse_int(&s) {
            Some(n) => { sum = sum + n },
            None => { failed = failed + 1 },
        }
        drop s
    }
    drop v
    print(sum)
    print(failed)
}
