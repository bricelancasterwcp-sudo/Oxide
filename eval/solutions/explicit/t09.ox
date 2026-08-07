fn main() {
    let v = push(push(push(push(push(vec(), 1), 2), 3), 4), 5)
    let i = len(&v) - 1
    while i >= 0 {
        match get(&v, i) {
            Some(x) => print(x),
            None => print(0),
        }
        i = i - 1
    }
    drop v
}
