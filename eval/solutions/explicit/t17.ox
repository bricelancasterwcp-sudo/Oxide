fn fetch(v: &Vec<Int>, i: Int) -> Int {
    match get(&v, i) {
        Some(x) => x,
        None => -1,
    }
}

fn main() {
    let v = push(push(push(vec(), 10), 20), 30)
    print(fetch(&v, 1))
    print(fetch(&v, 5))
    drop v
}
