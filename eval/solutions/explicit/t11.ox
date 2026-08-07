fn min_above(v: &Vec<Int>, floor: Int) -> Int {
    let best = 1000000
    for x in &v {
        if x > floor && x < best {
            best = x
        }
    }
    best
}

fn main() {
    let v = push(push(push(push(push(push(vec(), 5), 3), 8), 1), 9), 2)
    let last = -1000000
    let i = 0
    while i < len(&v) {
        let m = min_above(&v, last)
        print(m)
        last = m
        i = i + 1
    }
    drop v
}
