fn main() {
    let v = push(push(vec(), 7), 8)
    match get(&v, 0) {
        Some(x) => print(x),
        None => print(0),
    }
    match get(&v, 9) {
        Some(y) => print(y),
        None => print(0),
    }
    drop v
}
