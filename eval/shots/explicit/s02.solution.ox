fn main() {
    let v = push(push(push(push(vec(), 2), 5), 1), 6)
    let sum = 0
    let min = 1000000
    for x in &v {
        sum = sum + x
        if x < min {
            min = x
        }
    }
    drop v
    print(sum)
    print(min)
}
